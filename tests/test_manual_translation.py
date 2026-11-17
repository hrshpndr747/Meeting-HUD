from queue import Queue
from threading import Event
from types import SimpleNamespace
import ctypes
from ctypes import wintypes
import time
import unittest
from unittest.mock import Mock, patch
import httpx
import numpy as np

from support import APP, wait_for
from PyQt6.QtTest import QTest
from audio_dsp import StreamingResampler
from config import AppConfig
from dictation_capture import DictationCaptureWorker, UtteranceBuffer
from pipeline import AudioChunk, Mode, PipelineState, Source, TextRequest
from speech_engine import LocalWhisperBackend, SpeechWorker
from text_translation import TextTranslator, TranslationCancelled, TranslationError
from test_capture import FakePortAudio
from test_dsp import tone
from test_native_and_ui import FakeGuard
from ui_overlay import Overlay


class ManualCaptureTests(unittest.TestCase):
    def start_worker(self):
        FakePortAudio.streams, FakePortAudio.instances = [], []
        module = SimpleNamespace(PyAudio=FakePortAudio, paWASAPI=13, paFloat32=1, paContinue=0)
        patcher = patch.dict("sys.modules", {"pyaudiowpatch": module})
        patcher.start()
        self.addCleanup(patcher.stop)
        state, jobs = PipelineState(), Queue(4)
        state.update(mode=Mode.TRANSLATION, ready=True)
        worker = DictationCaptureWorker(AppConfig(), state, jobs)
        finished = []
        worker.finished_recording.connect(lambda *args: finished.append(args))
        def cleanup():
            worker.request_stop()
            self.assertTrue(worker.wait(3000))
        self.addCleanup(cleanup)
        worker.start()
        return state, jobs, worker, finished

    def test_idle_translation_never_opens_a_microphone(self):
        state, jobs, worker, finished = self.start_worker()
        QTest.qWait(120)
        self.assertEqual(FakePortAudio.instances, [])
        self.assertTrue(jobs.empty())

    def test_explicit_stop_keeps_pauses_and_final_audio_frames(self):
        state, jobs, worker, finished = self.start_worker()
        epoch = state.begin_recording().epoch
        worker.request_start()
        wait_for(lambda: bool(FakePortAudio.streams))
        stream = FakePortAudio.streams[0]
        audio = np.concatenate((tone(.4, 44100), np.zeros(44100, dtype="<f4"), tone(.4, 44100)))
        for offset in range(0, len(audio), 882):
            stream.settings["stream_callback"](audio[offset:offset+882].tobytes(), 882, {}, 0)
        QTest.qWait(100)
        self.assertTrue(jobs.empty())
        self.assertTrue(state.snapshot().recording)
        state.finish_recording(epoch)
        wait_for(lambda: bool(finished))
        self.assertTrue(finished[0][1])
        chunk = jobs.get_nowait()
        self.assertEqual(len(chunk.audio), 28800)
        self.assertTrue(stream.closed)
        self.assertTrue(state.accepts(epoch, Source.MIC))
        self.assertFalse(state.snapshot().recording)
        self.assertLess(np.max(np.abs(chunk.audio[9000:18000])), .001)

    def test_cancel_discards_recording_instead_of_translating_it(self):
        state, jobs, worker, finished = self.start_worker()
        state.begin_recording()
        worker.request_start()
        wait_for(lambda: bool(FakePortAudio.streams))
        callback = FakePortAudio.streams[0].settings["stream_callback"]
        callback(tone(.6, 44100).tobytes(), 26460, {}, 0)
        state.invalidate()
        wait_for(lambda: bool(finished))
        self.assertTrue(jobs.empty())
        self.assertFalse(finished[0][1])

    def test_lost_frames_fail_recording_without_stopping_live_engine(self):
        state, jobs, worker, finished = self.start_worker()
        state.begin_recording()
        worker.request_start()
        wait_for(lambda: bool(FakePortAudio.streams))
        FakePortAudio.streams[0].settings["stream_callback"](tone(.5, 44100).tobytes(), 22050, {}, 1)
        wait_for(lambda: bool(finished))
        self.assertTrue(jobs.empty())
        self.assertFalse(finished[0][1])
        self.assertTrue(state.snapshot().ready)

    def test_resampler_flush_preserves_original_duration_and_last_word(self):
        for rate in (16000, 44100, 48000):
            with self.subTest(rate=rate):
                signal = tone(.613, rate)
                resampler = StreamingResampler(rate, 1)
                output = np.concatenate((resampler.feed(signal.tobytes()), resampler.flush()))
                self.assertLessEqual(abs(len(output) - .613 * 16000), 1)
                self.assertGreater(float(np.sqrt(np.mean(output[-200:]**2))), .1)
                self.assertEqual(len(resampler.flush()), 0)


class TwoStageTests(unittest.TestCase):
    def run_job(self, job_builder, cancel_in_translator=False):
        state, jobs = PipelineState(), Queue(4)
        state.update(mode=Mode.TRANSLATION)
        order, results, done = [], [], []
        backend = SimpleNamespace(load=lambda: "Test", recognize=Mock(return_value="कल तीन बजे मिलते हैं।"), close=Mock())
        def translate(text, epoch):
            order.append(("translate", text))
            if cancel_in_translator:
                state.invalidate()
            return "Let us meet tomorrow at three."
        translator = SimpleNamespace(translate=translate, close=Mock())
        worker = SpeechWorker(AppConfig(), state, jobs, backend=backend, translator=translator)
        worker.recognized.connect(lambda item: order.append(("recognized", item.text)))
        worker.result.connect(results.append)
        worker.dictation_done.connect(lambda *args: done.append(args))
        def cleanup():
            worker.request_stop()
            self.assertTrue(worker.wait(3000))
        self.addCleanup(cleanup)
        worker.start()
        wait_for(lambda: state.snapshot().ready)
        jobs.put(job_builder(state))
        wait_for(lambda: bool(done))
        return state, backend, order, results, done

    def test_english_is_generated_from_the_recognized_source_text(self):
        def job(state):
            return AudioChunk(tone(1), Source.MIC, Mode.TRANSLATION, state.snapshot().epoch,
                              time.monotonic(), time.monotonic())
        state, backend, order, results, done = self.run_job(job)
        self.assertEqual(results[0].text, "Let us meet tomorrow at three.")
        self.assertEqual([text for stage, text in order], ["कल तीन बजे मिलते हैं।", "कल तीन बजे मिलते हैं।"])
        backend.recognize.assert_called_once()

    def test_edit_and_retranslate_uses_new_text_without_redecoding_audio(self):
        _, backend, order, results, _ = self.run_job(lambda state: TextRequest("कल चार बजे मिलते हैं।", state.snapshot().epoch, time.monotonic()))
        backend.recognize.assert_not_called()
        self.assertEqual(order[0][1], "कल चार बजे मिलते हैं।")
        self.assertEqual(len(results), 1)

    def test_cancel_during_translation_prevents_old_result(self):
        _, _, _, results, _ = self.run_job(lambda state: TextRequest("पुराना पाठ", state.snapshot().epoch, time.monotonic()), True)
        self.assertEqual(results, [])


class LocalTranslatorTests(unittest.TestCase):
    def test_free_defaults_and_no_cloud_requests(self):
        config = AppConfig()
        self.assertEqual((config.dictation_backend, config.text_translator, config.dictation_model), ("local", "ollama", "large-v3"))
        state = PipelineState()
        snapshot = state.update(ready=True, mode=Mode.TRANSLATION)
        requests = []
        def handler(request):
            requests.append(request)
            if request.url.path == "/api/show":
                return httpx.Response(200, json={"capabilities": ["completion"]})
            return httpx.Response(200, json={"message": {"content": "I cannot come tomorrow."}, "done_reason": "stop"})
        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        translator = TextTranslator(config, Event(), state, client)
        self.assertEqual(translator.translate("मैं कल नहीं आ सकता।", snapshot.epoch), "I cannot come tomorrow.")
        self.assertEqual([r.url.path for r in requests], ["/api/show", "/api/chat"])
        self.assertTrue(all(r.url.host == "127.0.0.1" for r in requests))
        self.assertNotIn("Authorization", requests[-1].headers)
        import json
        body = json.loads(requests[-1].read())
        self.assertEqual(body["messages"][-1]["content"], "मैं कल नहीं आ सकता।")
        self.assertEqual(body["keep_alive"], 0)

    def test_local_failure_never_falls_back_to_a_cloud_endpoint(self):
        state = PipelineState()
        snapshot = state.update(ready=True, mode=Mode.TRANSLATION)
        client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
        self.addCleanup(client.close)
        with self.assertRaises(TranslationError):
            TextTranslator(AppConfig(), Event(), state, client).translate("test", snapshot.epoch)

    def test_truncated_translation_is_not_presented_as_complete(self):
        state = PipelineState()
        snapshot = state.update(ready=True, mode=Mode.TRANSLATION)
        client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200,
            json={"capabilities": ["completion"]} if r.url.path == "/api/show" else
            {"message":{"content":"Partial"},"done_reason":"length"})))
        self.addCleanup(client.close)
        with self.assertRaisesRegex(TranslationError, "cut off"):
            TextTranslator(AppConfig(), Event(), state, client).translate("test", snapshot.epoch)


class UIRegressionTests(unittest.TestCase):
    def setUp(self):
        self.window = Overlay(FakeGuard())
        self.addCleanup(self.window.prepare_shutdown)

    def test_unhandled_native_events_return_false_without_base_binding(self):
        self.assertEqual(self.window.nativeEvent(b"windows_generic_MSG", None), (False, 0))
        msg = wintypes.MSG()
        msg.message = 0x0081  # WM_NCCREATE during first winId realization
        self.assertEqual(self.window.nativeEvent(b"windows_generic_MSG", ctypes.addressof(msg)), (False, 0))

    def test_translation_controls_are_idle_and_editable_after_processing(self):
        self.window.select_mode(Mode.TRANSLATION)
        self.window.set_record_state(False, False, True)
        self.assertEqual(self.window.record_button.text(), "Record")
        self.assertEqual(self.window.record_clock.text(), "MIC OFF")
        self.window.set_record_state(True, False, True)
        self.assertEqual(self.window.record_button.text(), "Stop")
        self.assertTrue(self.window.source_edit.isReadOnly())
        self.window.set_record_state(False, False, True)
        self.window.set_recognized("Correct these words")
        self.assertFalse(self.window.source_edit.isReadOnly())
        self.assertTrue(self.window.retranslate_button.isEnabled())

    def test_minimize_has_a_restore_path_and_does_not_close_the_app(self):
        self.window.show()
        closed, hidden = [], []
        self.window.shutdown_requested.connect(lambda: closed.append(True))
        self.window.local_hidden.connect(lambda: hidden.append(True))
        self.window.minimize_button.click()
        self.assertFalse(self.window.isVisible())
        self.assertFalse(closed)
        self.assertTrue(hidden)
        self.window.toggle_visibility()
        self.assertTrue(self.window.isVisible())


if __name__ == "__main__":
    unittest.main()
