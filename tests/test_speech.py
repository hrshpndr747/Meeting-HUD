from dataclasses import replace
from queue import Queue
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import httpx
import numpy as np

from support import wait_for
from config import AppConfig
from pipeline import AudioChunk, Mode, PipelineState, Source
from speech_engine import Cancelled, FatalBackendError, GroqWhisperBackend, LocalWhisperBackend, SpeechWorker, remove_overlap


def chunk_for(state, mode=Mode.TRANSCRIPT):
    snapshot = state.update(ready=True, mode=mode)
    return AudioChunk(np.zeros(16000, dtype=np.float32), Source.MIC, mode, snapshot.epoch,
                      time.monotonic() - 0.3, time.monotonic())


class FakeBackend:
    def __init__(self):
        self.entered = Event()
        self.release = Event()
        self.closed = False
        self.calls = 0

    def load(self):
        return "Test engine"

    def recognize(self, chunk):
        self.calls += 1
        self.entered.set()
        if not self.release.wait(3):
            raise RuntimeError("Test did not release inference")
        return "Decoded sentence."

    def close(self):
        self.closed = True


class WorkerTests(unittest.TestCase):
    def start_worker(self):
        state, queue, backend = PipelineState(), Queue(4), FakeBackend()
        worker = SpeechWorker(AppConfig(), state, queue, backend)
        results, faults = [], []
        worker.result.connect(results.append)
        worker.fault.connect(faults.append)
        def cleanup():
            backend.release.set()
            worker.request_stop()
            self.assertTrue(worker.wait(4000))
        self.addCleanup(cleanup)
        worker.start()
        wait_for(lambda: state.snapshot().ready)
        return state, queue, backend, worker, results, faults

    def test_real_qthread_emits_result_and_closes_cooperatively(self):
        state, queue, backend, worker, results, faults = self.start_worker()
        queue.put(chunk_for(state))
        wait_for(backend.entered.is_set)
        backend.release.set()
        wait_for(lambda: len(results) == 1)
        self.assertEqual(results[0].text, "Decoded sentence.")
        self.assertEqual(results[0].epoch, state.snapshot().epoch)
        self.assertFalse(faults)
        worker.request_stop()
        wait_for(lambda: not worker.isRunning())
        self.assertTrue(backend.closed)

    def test_mode_change_during_inference_discards_old_result(self):
        state, queue, backend, worker, results, faults = self.start_worker()
        queue.put(chunk_for(state))
        wait_for(backend.entered.is_set)
        state.update(mode=Mode.TRANSLATION)
        backend.release.set()
        worker.request_stop()
        wait_for(lambda: not worker.isRunning())
        self.assertEqual(results, [])
        self.assertFalse(faults)

    def test_stale_audio_never_reaches_decoder(self):
        state, queue, backend, worker, results, faults = self.start_worker()
        stale = replace(chunk_for(state), queued_at=time.monotonic() - 10)
        notices = []
        worker.status.connect(notices.append)
        queue.put(stale)
        wait_for(lambda: any("Old queued" in message for message in notices))
        self.assertEqual(backend.calls, 0)
        self.assertEqual(results, [])

    def test_obsolete_fatal_error_does_not_fault_the_new_mode(self):
        state, queue, backend, worker, results, faults = self.start_worker()
        entered, release, completed = Event(), Event(), Event()
        worker.busy.connect(lambda active: completed.set() if not active else None)
        def failing(chunk):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("Test failed to release inference")
            raise FatalBackendError("Old live-provider failure")
        backend.recognize = failing
        queue.put(chunk_for(state))
        wait_for(entered.is_set)
        state.update(mode=Mode.TRANSLATION)
        release.set()
        wait_for(completed.is_set)
        self.assertTrue(worker.isRunning())
        self.assertTrue(state.snapshot().ready)
        self.assertFalse(faults)
        worker.request_stop()
        wait_for(lambda: not worker.isRunning())
        self.assertFalse(faults)


class CloudTests(unittest.TestCase):
    def backend(self, handler, prompt=""):
        state = PipelineState()
        config = AppConfig(backend="groq", model="whisper-large-v3", dictation_backend="groq",
                           dictation_model="whisper-large-v3", groq_api_key="test-key",
                           cloud_enabled=True, initial_prompt=prompt)
        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        backend = GroqWhisperBackend(config, Event(), state, client)
        backend.load()
        return backend, state

    def test_translation_route_wav_and_optional_glossary(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"text": " Thank you. "})
        backend, state = self.backend(handler, "Project Lotus, Rohan.")
        self.assertEqual(backend.recognize(chunk_for(state, Mode.TRANSLATION)), "Thank you.")
        request = requests[0]
        body = request.read()
        self.assertEqual(request.url.path, "/openai/v1/audio/transcriptions")
        self.assertIn(b"RIFF", body)
        self.assertIn(b"WAVE", body)
        self.assertIn(b"whisper-large-v3", body)
        self.assertIn(b"Project Lotus, Rohan.", body)
        self.assertIn(b'name="language"', body)

    def test_transcription_language_and_no_empty_prompt(self):
        requests = []
        backend, state = self.backend(lambda r: (requests.append(r) or httpx.Response(200, json={"text": "Hello"})))
        backend.config = replace(backend.config, transcript_language="en")
        backend.recognize(chunk_for(state))
        self.assertEqual(requests[0].url.path, "/openai/v1/audio/transcriptions")
        body = requests[0].read()
        self.assertIn(b'name="language"', body)
        self.assertNotIn(b'name="prompt"', body)

    def test_rate_limit_retries_once_with_bounded_delay(self):
        count = 0
        def handler(request):
            nonlocal count
            count += 1
            return (httpx.Response(429, headers={"retry-after": "900"}) if count == 1
                    else httpx.Response(200, json={"text": "Recovered"}))
        backend, state = self.backend(handler)
        backend.stop_event = SimpleNamespace(is_set=lambda: False, wait=Mock(return_value=False))
        self.assertEqual(backend.recognize(chunk_for(state)), "Recovered")
        self.assertEqual(count, 2)
        backend.stop_event.wait.assert_called_once_with(3.0)

    def test_authorization_error_is_fatal_without_leaking_response_body(self):
        backend, state = self.backend(lambda request: httpx.Response(401, text="test-key private data"))
        with self.assertRaises(FatalBackendError) as caught:
            backend.recognize(chunk_for(state))
        self.assertIn("401", str(caught.exception))
        self.assertNotIn("test-key", str(caught.exception))

    def test_invalid_response_and_obsolete_request(self):
        handler = Mock(return_value=httpx.Response(200, json={"text": 123}))
        backend, state = self.backend(handler)
        chunk = chunk_for(state)
        with self.assertRaisesRegex(RuntimeError, "invalid text response"):
            backend.recognize(chunk)
        state.update(paused=True)
        with self.assertRaises(Cancelled):
            backend.recognize(chunk)
        self.assertEqual(handler.call_count, 1)


class LocalTests(unittest.TestCase):
    def test_both_modes_use_silero_and_translation_keeps_real_thank_you(self):
        backend = LocalWhisperBackend(AppConfig(dictation_model="small"), lambda message: None)
        calls = []
        def decode(audio, **kwargs):
            calls.append(kwargs)
            return iter([SimpleNamespace(text=" Thank you. ")]), None
        backend.model = SimpleNamespace(transcribe=decode)
        state = PipelineState()
        for mode in Mode:
            self.assertEqual(backend.recognize(chunk_for(state, mode)), "Thank you.")
        self.assertTrue(all(call["vad_filter"] for call in calls))
        self.assertEqual([call["task"] for call in calls], ["transcribe", "transcribe"])
        self.assertEqual([call["language"] for call in calls], [None, "hi"])
        self.assertEqual([call["beam_size"] for call in calls], [1, 5])
        self.assertEqual([call["condition_on_previous_text"] for call in calls], [False, True])
        self.assertTrue(all("initial_prompt" not in call for call in calls))

    def test_glossary_is_fixed_not_accumulated_from_prior_output(self):
        config = AppConfig(initial_prompt="Project Lotus, Rohan.", dictation_model="small")
        backend = LocalWhisperBackend(config, lambda message: None)
        decoder = Mock(side_effect=lambda *args, **kwargs: (iter([SimpleNamespace(text="Unrelated output.")]), None))
        backend.model = SimpleNamespace(transcribe=decoder)
        state = PipelineState()
        for mode in Mode:
            backend.recognize(chunk_for(state, mode))
        self.assertEqual([call.kwargs["initial_prompt"] for call in decoder.call_args_list],
                         [config.initial_prompt, config.initial_prompt])

    def test_startup_warmup_consumes_decoder_and_silero_generators(self):
        consumed = []
        def transcribe(audio, **options):
            self.assertEqual(audio.dtype, np.float32)
            self.assertEqual(len(audio), 16000)
            def segments():
                consumed.append(options["vad_filter"])
                yield SimpleNamespace(text="")
            return segments(), None
        factory = Mock(return_value=SimpleNamespace(transcribe=transcribe))
        backend = LocalWhisperBackend(AppConfig(), lambda message: None)
        with patch.dict("sys.modules", {"faster_whisper": SimpleNamespace(WhisperModel=factory)}):
            backend._load_on("cuda")
        self.assertEqual(consumed, [False, True])
        self.assertEqual(factory.call_args.kwargs["compute_type"], "int8_float16")
        self.assertEqual(backend.device, "cuda")

    def test_cuda_startup_failure_falls_back_with_actionable_notice(self):
        notices, attempts = [], []
        backend = LocalWhisperBackend(AppConfig(), notices.append)
        def load(device):
            attempts.append(device)
            if device == "cuda":
                raise OSError("hidden native body")
            backend.device = "cpu"
        fake_ct2 = SimpleNamespace(get_cuda_device_count=lambda: 1)
        with patch.dict("sys.modules", {"ctranslate2": fake_ct2}), patch.object(backend, "_cuda_preflight"), patch.object(backend, "_load_on", side_effect=load):
            self.assertIn("CPU", backend.load())
        self.assertEqual(attempts, ["cuda", "cpu"])
        self.assertIn("warm-up", backend.fallback_reason)
        self.assertIn("--diagnose", notices[0])
        self.assertNotIn("hidden native body", notices[0])

    def test_gpu_runtime_failure_retries_chunk_once_on_cpu(self):
        notices = []
        backend = LocalWhisperBackend(AppConfig(), notices.append)
        backend.device = "cuda"
        backend.model = object()
        state = PipelineState()
        with patch.object(backend, "_decode", side_effect=[RuntimeError("GPU failed"), "Recovered."]) as decode, patch.object(backend, "_load_on") as load:
            self.assertEqual(backend.recognize(chunk_for(state)), "Recovered.")
        load.assert_called_once_with("cpu", "small")
        self.assertEqual(decode.call_count, 2)
        self.assertIn("CPU fallback", notices[0])

    def test_overlap_cleanup_requires_multiple_matching_words(self):
        self.assertEqual(remove_overlap("We agree on the plan.", "the plan is ready."), "is ready.")
        self.assertEqual(remove_overlap("Yes.", "Yes, please."), "Yes, please.")
        self.assertEqual(remove_overlap("Budget approved.", "Different sentence."), "Different sentence.")


if __name__ == "__main__":
    unittest.main()
