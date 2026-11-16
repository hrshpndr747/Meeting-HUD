"""v1.4 audio/protocol regressions: no credentials, network, microphone or GPU."""
import asyncio
import base64
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
from queue import Queue
from threading import Event
import time
import unittest
from unittest.mock import patch
import wave
import zipfile
import tempfile
import httpx
import numpy as np

from support import APP
from config import AppConfig
from gemini_live import ContinuousProcessor, GeminiLiveWorker, LiveAudioBus, LiveFailure, LiveFrame, LivePartial
from gemini_protocol import input_transcripts, interaction_text
from gemini_speech import GeminiSpeechBackend
from microphones import MIC_LEASE, MicRequest, MicWorker, MicrophoneError, resolve_microphone
from model_catalog import CatalogRequest, fetch_models
from network_io import provider_error, request_json
from noise_suppression import DenoisingResampler, NoiseError, RNNoiseFrames
from pipeline import Mode, PipelineState, Source
from preferences import settings_path
from speech_engine import Cancelled, FatalBackendError, SpeechWorker
from test_native_and_ui import FakeGuard
from test_speech import chunk_for
from ui_overlay import Overlay
from ui_settings import SettingsPanel


def gemini_config(**kwargs):
    return replace(AppConfig(backend="gemini", model="gemini-3.5-transcribe-live",
                            dictation_backend="gemini", dictation_model="gemini-3.5-transcribe",
                            gemini_api_key="test-secret", cloud_enabled=True), **kwargs)


class SpeechAndSettingsTests(unittest.TestCase):
    def client(self, handler):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return client

    def test_gemini_available_in_both_speech_selectors_and_models_are_independent(self):
        panel = SettingsPanel(AppConfig())
        self.addCleanup(panel.deleteLater)
        for stage, model in (("live", "gemini-3.5-transcribe-live"), ("dictation", "gemini-3.5-transcribe")):
            panel.select_stage(stage)
            self.assertFalse(panel.provider_buttons["gemini"].isHidden())
            panel.select_provider("gemini")
            self.assertEqual(panel.model_edit.text(), model)
        panel.select_stage("text")
        self.assertEqual(panel.providers["text"], "ollama")
        self.assertFalse(panel.cloud.isChecked())

    def test_model_validation_prevents_using_text_or_live_agent_for_captions(self):
        for name in ("gemini-2.5-flash", "gemini-native-audio-live", "../../secret", "gemini-3.5-transcribe"):
            with self.assertRaises(ValueError):
                gemini_config(model=name)
        with self.assertRaises(ValueError):
            gemini_config(dictation_model="gemini-3.5-transcribe-live")

    def test_gemini_catalog_is_stage_specific_without_blanket_25_ban(self):
        rows = [{"name": "models/" + name, "supportedGenerationMethods": methods} for name, methods in (
            ("gemini-3.5-transcribe-live", ["bidiGenerateContent"]),
            ("gemini-3.5-transcribe", []), ("gemini-2.5-flash", ["generateContent"]),
            ("gemini-3.5-flash-tts", ["generateContent"]), ("gemini-live-agent", ["bidiGenerateContent"]))]
        client = self.client(lambda r: httpx.Response(200, json={"models": rows}))
        self.assertEqual(fetch_models(CatalogRequest(1, "gemini", "live", "key"), client), ("gemini-3.5-transcribe-live",))
        self.assertEqual(fetch_models(CatalogRequest(2, "gemini", "dictation", "key"), client),
                         ("gemini-2.5-flash", "gemini-3.5-transcribe"))
        self.assertEqual(fetch_models(CatalogRequest(3, "gemini", "text", "key"), client), ("gemini-2.5-flash",))

    def test_complete_utterance_wav_inline_no_storage_and_hinglish_hints(self):
        state = PipelineState()
        source = "कल meeting तीन बजे है, चार बजे नहीं।"
        def handler(request):
            self.assertEqual(request.url.path, "/v1beta/interactions")
            self.assertNotIn("test-secret", str(request.url))
            body = json.loads(request.read())
            self.assertFalse(body["store"])
            self.assertEqual(body["generation_config"]["transcription_config"]["mode"], {"type": "verbatim"})
            self.assertEqual(body["generation_config"]["transcription_config"]["language_codes"], ["hi-IN", "en-IN"])
            self.assertEqual(body["generation_config"]["transcription_config"]["custom_vocabulary"], ["Rohan", "Kubernetes"])
            with wave.open(io.BytesIO(base64.b64decode(body["input"][0]["data"]))) as audio:
                self.assertEqual((audio.getframerate(), audio.getnchannels(), audio.getsampwidth(), audio.getnframes()),
                                 (16000, 1, 2, 16000))
            return httpx.Response(200, json={"status": "completed", "steps": [
                {"type": "model_output", "content": [{"type": "text", "text": source}]}]})
        backend = GeminiSpeechBackend(gemini_config(initial_prompt="Rohan, Kubernetes"), Event(), state, self.client(handler))
        self.assertEqual(backend.recognize(chunk_for(state, Mode.TRANSLATION)), source)

    def test_flash_audio_recognition_uses_audio_and_never_its_thoughts(self):
        state = PipelineState()
        def handler(request):
            body = json.loads(request.read())
            self.assertIn("inlineData", body["contents"][0]["parts"][0])
            return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": [
                {"text": "private thought", "thought": True}, {"text": "नहीं"}]}}]})
        backend = GeminiSpeechBackend(gemini_config(dictation_model="gemini-2.5-flash"), Event(), state, self.client(handler))
        self.assertEqual(backend.recognize(chunk_for(state, Mode.TRANSLATION)), "नहीं")

    def test_audio_cloud_opt_in_and_cancel_checked_before_request(self):
        state = PipelineState()
        backend = GeminiSpeechBackend(gemini_config(cloud_enabled=False), Event(), state,
                                       self.client(lambda r: self.fail("Unexpected audio upload")))
        chunk = chunk_for(state, Mode.TRANSLATION)
        with self.assertRaises(FatalBackendError):
            backend.recognize(chunk)
        backend.config = gemini_config()
        state.invalidate()
        with self.assertRaises(Cancelled):
            backend.recognize(chunk)

    def test_cancel_during_response_cannot_publish_old_recognition(self):
        state = PipelineState()
        def handler(request):
            state.invalidate()
            return httpx.Response(200, json={"status": "completed", "steps": []})
        backend = GeminiSpeechBackend(gemini_config(), Event(), state, self.client(handler))
        with self.assertRaises(Cancelled):
            backend.recognize(chunk_for(state, Mode.TRANSLATION))

    def test_same_gemini_provider_still_uses_different_live_and_dictation_backends(self):
        worker = SpeechWorker(gemini_config(), PipelineState(), Queue(4))
        worker._backend = worker._make_backend("gemini")
        backend = worker._for_mode(Mode.TRANSLATION)
        self.addCleanup(backend.close)
        self.assertTrue(worker._backend.live)
        self.assertFalse(backend.live)
        self.assertIsNot(backend, worker._backend)

    def test_noncompleted_interaction_is_rejected(self):
        for data in ({"status": "requires_action"}, {"status": "failed"}, {}):
            with self.assertRaises(ValueError):
                interaction_text(data)

    def test_error_is_actionable_without_key_prefix_or_echoed_speech(self):
        secret = "SECRET_AT_TRUNCATION_BOUNDARY_1234567890"
        data = {"error": {"message": "x" * 195 + secret + " <b>my private speech</b> not available to new users"}}
        message = provider_error("Gemini", 404, data, (secret,))
        self.assertIn("new accounts", message)
        for forbidden in (secret, "SECRET", "private speech", "<b>"):
            self.assertNotIn(forbidden, message)

    def test_bounded_malformed_error_body_still_has_status(self):
        for response in (httpx.Response(400, text="invalid"), httpx.Response(403, content=b"x" * 20000)):
            client = self.client(lambda r, value=response: value)
            code, data, _ = request_json(client, "GET", "https://example.invalid")
            self.assertIn(code, (400, 403))
            self.assertIsNone(data)

    def test_appdata_does_not_evaluate_broken_home_and_never_uses_shared_temp(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"APPDATA": root}, clear=True), \
                patch("preferences.Path.home", side_effect=RuntimeError("missing home")):
            self.assertEqual(settings_path(), Path(root) / "MeetingHUD" / "settings.json")
        with patch.dict(os.environ, {}, clear=True), patch("preferences.Path.home", side_effect=RuntimeError()):
            with self.assertRaises(ValueError):
                settings_path()


class FakeSocket:
    def __init__(self):
        self.messages = asyncio.Queue()
        self.sent = []
        self.closed = False
    async def send(self, value):
        self.sent.append(json.loads(value))
    async def recv(self):
        return await self.messages.get()
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        self.closed = True


async def until(predicate):
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(.005)
    raise AssertionError("Async condition timed out")


class LiveProtocolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = PipelineState()
        self.epoch = self.state.update(ready=True).epoch
        self.bus = LiveAudioBus()
        self.identity = self.bus.open(Source.MIC, self.epoch)
        self.socket = FakeSocket()
        self.worker = GeminiLiveWorker(gemini_config(), self.state, self.bus,
                                        connector=lambda *a, **k: self.socket)

    async def test_setup_handshake_precedes_audio_and_pcm_is_correct(self):
        samples = np.full(1600, .5, dtype=np.float32)
        self.bus.publish(Source.MIC, self.identity, samples, time.monotonic())
        task = asyncio.create_task(self.worker._session(Source.MIC, self.identity))
        try:
            await until(lambda: len(self.socket.sent) == 1)
            self.assertIn("setup", self.socket.sent[0])
            await asyncio.sleep(.025)
            self.assertEqual(len(self.socket.sent), 1)
            await self.socket.messages.put(json.dumps({"setupComplete": {}}))
            await until(lambda: len(self.socket.sent) > 1)
            audio = self.socket.sent[1]["realtimeInput"]["audio"]
            self.assertEqual(audio["mimeType"], "audio/pcm;rate=16000")
            pcm = np.frombuffer(base64.b64decode(audio["data"]), dtype="<i2")
            self.assertEqual(len(pcm), 1600)
            self.assertTrue(np.all(pcm == 16383))
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(self.socket.closed)

    async def test_interim_replacement_and_final_after_turncomplete_ignore_model_reply(self):
        partials, results = [], []
        self.worker.partial.connect(partials.append)
        self.worker.result.connect(results.append)
        task = asyncio.create_task(self.worker._receive(self.socket, Source.MIC, self.identity))
        try:
            for content in ({"interimInputTranscription": {"text": "कल"}},
                            {"interimInputTranscription": {"text": "कल तीन"}},
                            {"turnComplete": True, "outputTranscription": {"text": "Wrong model answer"}},
                            {"inputTranscription": {"text": "कल तीन बजे।"}}):
                await self.socket.messages.put(json.dumps({"serverContent": content}))
            await until(lambda: len(results) == 1)
            self.assertEqual([p.text for p in partials], ["कल", "कल तीन", ""])
            self.assertEqual(results[0].text, "कल तीन बजे।")
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_pause_cancels_connection_and_never_sends_new_audio(self):
        await self.socket.messages.put(json.dumps({"setupComplete": {}}))
        task = asyncio.create_task(self.worker._run_async())
        try:
            await until(lambda: bool(self.socket.sent))
            self.state.update(paused=True)
            self.bus.publish(Source.MIC, self.identity, np.ones(1600), time.monotonic())
            await until(lambda: self.socket.closed)
            self.assertTrue(all("realtimeInput" not in message for message in self.socket.sent))
        finally:
            self.worker.request_stop()
            await task

    async def test_stale_audio_and_sequence_gap_force_connection_reset(self):
        self.bus.queues[Source.MIC].put(LiveFrame(*self.identity, 1, b"\0" * 3200, time.monotonic() - 3))
        with self.assertRaises(LiveFailure):
            await self.worker._send(self.socket, Source.MIC, self.identity)
        for seq in (1, 3):
            self.bus.queues[Source.MIC].put(LiveFrame(*self.identity, seq, b"\0" * 3200, time.monotonic()))
        with self.assertRaises(LiveFailure):
            await self.worker._send(self.socket, Source.MIC, self.identity)
        self.assertEqual(len(self.socket.sent), 1)

    async def test_bad_setup_is_fatal_and_uploads_nothing(self):
        await self.socket.messages.put(json.dumps({"error": {"code": 403, "message": "test-secret private"}}))
        with self.assertRaises(LiveFailure) as result:
            await self.worker._session(Source.MIC, self.identity)
        self.assertTrue(result.exception.permanent)
        self.assertNotIn("test-secret", str(result.exception))
        self.assertEqual(len(self.socket.sent), 1)

    async def test_obsolete_permanent_error_cannot_fault_new_translation_mode(self):
        faults = []
        self.worker.fault.connect(faults.append)
        async def old_session(source, identity):
            self.state.update(mode=Mode.TRANSLATION)
            raise LiveFailure("Old account failure", permanent=True)
        self.worker._session = old_session
        task = asyncio.create_task(self.worker._run_async())
        try:
            await until(lambda: self.state.snapshot().mode == Mode.TRANSLATION)
            await asyncio.sleep(.05)
            self.assertFalse(faults)
            self.assertFalse(task.done())
        finally:
            self.worker.request_stop()
            await task

    async def test_goaway_requests_reconnect_without_playing_generated_audio(self):
        await self.socket.messages.put(json.dumps({"goAway": {"timeLeft": "10s"}, "serverContent": {
            "modelTurn": {"parts": [{"inlineData": {"data": "should never play"}}]}}}))
        with self.assertRaises(LiveFailure):
            await self.worker._receive(self.socket, Source.MIC, self.identity)


class AudioTests(unittest.TestCase):
    def test_continuous_stream_preserves_silence_and_100ms_frames(self):
        dsp = ContinuousProcessor(48000, 2)
        self.addCleanup(dsp.close)
        parts = []
        for i in range(60):
            parts.extend(dsp.feed(np.zeros((960, 2), dtype="<f4").tobytes(), i * .02))
        self.assertGreaterEqual(len(parts), 10)
        self.assertTrue(all(len(p.audio) == 1600 and np.all(p.audio == 0) for p in parts))

    def test_bus_sources_are_separate_bounded_and_old_device_is_rejected(self):
        bus = LiveAudioBus()
        first = bus.open(Source.MIC, 1)
        system = bus.open(Source.SYSTEM, 1)
        for i in range(40):
            bus.publish(Source.MIC, first, np.ones(1600), time.monotonic())
        self.assertEqual(bus.queues[Source.MIC].qsize(), 20)
        self.assertEqual(bus.queues[Source.SYSTEM].qsize(), 0)
        bus.open(Source.MIC, 1)
        bus.publish(Source.MIC, first, np.ones(1600), time.monotonic())
        self.assertTrue(bus.queues[Source.MIC].empty())
        self.assertEqual(bus.current(Source.SYSTEM), system)

    def test_slow_handshake_prunes_old_frames_without_losing_fresh_tail(self):
        bus = LiveAudioBus()
        identity = bus.open(Source.MIC, 5)
        bus.publish(Source.MIC, identity, np.zeros(1600), time.monotonic() - 4)
        bus.publish(Source.MIC, identity, np.ones(1600), time.monotonic())
        self.assertTrue(bus.discard_stale_prefix(Source.MIC, identity))
        self.assertEqual(bus.queues[Source.MIC].qsize(), 1)
        self.assertEqual(bus.queues[Source.MIC].get_nowait().sequence, 2)

    def test_explicit_microphone_survives_reordering_but_duplicate_missing_fail(self):
        class Audio:
            rows = []
            def get_host_api_info_by_type(self, kind):
                return {"index": 3, "deviceCount": len(self.rows)}
            def get_device_info_by_host_api_device_index(self, host, i):
                return self.rows[i]
        pa = Audio()
        mic = {"index": 8, "name": "Headset", "maxInputChannels": 1}
        pa.rows = [dict(mic, name="Other", index=2), mic]
        self.assertEqual(resolve_microphone(pa, 13, "wasapi:Headset")["index"], 8)
        pa.rows = [dict(mic, index=1), dict(mic, name="Other", index=9)]
        self.assertEqual(resolve_microphone(pa, 13, "wasapi:Headset")["index"], 1)
        for rows in ([], [mic, dict(mic, index=9)]):
            pa.rows = rows
            with self.assertRaises(MicrophoneError):
                resolve_microphone(pa, 13, "wasapi:Headset")

    def test_microphone_cancel_invalidates_even_a_request_already_dequeued(self):
        worker = MicWorker()
        worker.submit(MicRequest(1, test=True))
        revision, _ = worker.jobs.get_nowait()
        worker.cancel()
        worker.active_revision = revision
        self.assertTrue(worker._cancelled())

    def test_noise_tail_compensates_native_delay_without_losing_duration(self):
        class Delayed:
            previous = np.zeros(480, dtype=np.float32)
            closed = False
            def process(self, frame):
                out, self.previous = self.previous, frame.copy()
                return out
            def close(self):
                self.closed = True
        engine = Delayed()
        processor = DenoisingResampler(44100, 1, engine)
        signal = np.sin(np.arange(44100 + 171) * 2 * np.pi * 440 / 44100).astype("<f4") * .1
        parts = [processor.feed(signal[i:i+777].tobytes()) for i in range(0, len(signal), 777)]
        parts.append(processor.flush())
        output = np.concatenate(parts)
        expected = int(np.ceil(len(signal) * 16000 / 44100))
        self.assertLessEqual(abs(len(output) - expected), 1)
        self.assertGreater(np.max(np.abs(output[-160:])), .05)
        self.assertTrue(engine.closed)
        self.assertEqual(len(processor.flush()), 0)

    def test_missing_rnnoise_is_actionable_and_leaves_normal_resampling_usable(self):
        with self.assertRaisesRegex(NoiseError, "setup_noise"):
            RNNoiseFrames("/definitely/missing/rnnoise.dll")
        from noise_suppression import make_resampler
        self.assertGreater(len(make_resampler(16000, 1, False).feed(np.zeros(1600, dtype="<f4").tobytes())), 0)

    def test_noise_library_is_loaded_once_but_each_stream_frees_its_own_state(self):
        from unittest.mock import Mock
        import noise_suppression
        library = Mock()
        library.rnnoise_get_frame_size.return_value = 480
        library.rnnoise_create.side_effect = [101, 202]
        with patch.dict(noise_suppression._LIBRARIES, {}, clear=True), \
                patch.object(noise_suppression.ctypes, "CDLL", return_value=library) as load:
            first, second = RNNoiseFrames("/test/native.dll"), RNNoiseFrames("/test/native.dll")
            self.assertEqual(load.call_count, 1)
            first.close()
            second.close()
            first.close()
            self.assertEqual(library.rnnoise_destroy.call_count, 2)
            self.assertEqual([call.args[0] for call in library.rnnoise_destroy.call_args_list], [101, 202])

    def test_noise_installer_rejects_bad_digest_and_extracts_only_whitelisted_files(self):
        import install_noise
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr(install_noise.DLL_ENTRY, b"test DLL")
            output.writestr(install_noise.LICENSE_ENTRY, b"test license")
            output.writestr("../escape.txt", b"must not extract")
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                install_noise.install(root, archive.getvalue())
            self.assertFalse((Path(root) / "native").exists())
            with patch.object(install_noise, "SHA256", hashlib.sha256(archive.getvalue()).hexdigest()):
                install_noise.install(root, archive.getvalue())
            self.assertEqual({p.name for p in (Path(root) / "native").iterdir()}, {"rnnoise.dll", "PyRNNoise-LICENSE.txt"})

    def test_live_partial_ui_replaces_instead_of_appending_history(self):
        window = Overlay(guard=FakeGuard())
        self.addCleanup(window.prepare_shutdown)
        window.set_partial(LivePartial(1, Source.MIC, "पहला"))
        window.set_partial(LivePartial(1, Source.MIC, "दूसरा"))
        self.assertNotIn("पहला", window.partial_caption.text())
        self.assertIn("दूसरा", window.partial_caption.text())
        self.assertFalse(window.content.toPlainText())
        window.clear_partials()
        self.assertFalse(window.partial_caption.text())


class CaptureIntegrationTests(unittest.TestCase):
    def test_gemini_capture_produces_two_continuous_streams_without_whisper_jobs(self):
        from types import SimpleNamespace
        from support import wait_for
        from audio_capture import AudioCaptureWorker
        from test_capture import FakePortAudio, FakeWatcher
        FakePortAudio.streams, FakePortAudio.instances = [], []
        module = SimpleNamespace(PyAudio=FakePortAudio, paWASAPI=13, paFloat32=1, paContinue=0)
        state, jobs, bus = PipelineState(), Queue(4), LiveAudioBus()
        state.update(ready=True)
        worker = AudioCaptureWorker(gemini_config(), state, jobs, bus)
        with patch.dict("sys.modules", {"pyaudiowpatch": module}), patch("audio_capture.EndpointWatcher", FakeWatcher):
            worker.start()
            try:
                wait_for(lambda: len(FakePortAudio.streams) == 2)
                for stream in FakePortAudio.streams:
                    config = stream.settings
                    data = np.zeros((round(config["rate"] * .02), config["channels"]), dtype="<f4").tobytes()
                    for _ in range(7):
                        config["stream_callback"](data, 1, {}, 0)
                wait_for(lambda: all(not queue.empty() for queue in bus.queues.values()))
                self.assertTrue(jobs.empty())
                self.assertTrue(all(len(queue.get_nowait().pcm) == 3200 for queue in bus.queues.values()))
                state.update(mode=Mode.TRANSLATION)
                wait_for(lambda: all(bus.current(source) is None for source in Source))
            finally:
                worker.request_stop()
                self.assertTrue(worker.wait(3000))
        self.assertTrue(MIC_LEASE.acquire(blocking=False))
        MIC_LEASE.release()

    def test_mic_test_cancel_closes_stream_and_releases_ownership(self):
        from types import SimpleNamespace
        from support import wait_for
        from test_capture import FakePortAudio
        FakePortAudio.streams, FakePortAudio.instances = [], []
        module = SimpleNamespace(PyAudio=FakePortAudio, paWASAPI=13, paFloat32=1, paContinue=0)
        worker = MicWorker()
        with patch.dict("sys.modules", {"pyaudiowpatch": module}):
            worker.start()
            try:
                worker.submit(MicRequest(1, test=True))
                wait_for(lambda: len(FakePortAudio.streams) == 1)
                self.assertTrue(MIC_LEASE.locked())
                worker.cancel()
                wait_for(lambda: FakePortAudio.streams[0].closed)
            finally:
                worker.request_stop()
                self.assertTrue(worker.wait(3000))
        self.assertFalse(MIC_LEASE.locked())
        self.assertTrue(all(pa.terminated for pa in FakePortAudio.instances))


if __name__ == "__main__":
    unittest.main()
