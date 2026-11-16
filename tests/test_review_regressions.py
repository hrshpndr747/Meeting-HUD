"""Regressions found by the independent implementation review.

These exercise real worker threads and the Qt event queue, with fake hardware
and inference. They do not assert Windows/GPU/audio-device validation.
"""
from queue import Queue
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from support import APP, wait_for
from config import AppConfig
from dictation_capture import DictationCaptureWorker
from pipeline import AudioChunk, Mode, PipelineState, Source
from speech_engine import LocalWhisperBackend, SpeechWorker
from test_capture import FakePortAudio
from test_dsp import tone
from test_native_and_ui import FakeGuard
from ui_overlay import Overlay


class ReviewWorkerRegressions(unittest.TestCase):
    def test_delayed_capture_cleanup_cannot_resurrect_completed_translation(self):
        cleanup_entered, release_cleanup = Event(), Event()

        class SlowClosePortAudio(FakePortAudio):
            streams, instances = [], []

            def terminate(self):
                cleanup_entered.set()
                release_cleanup.wait(3)
                super().terminate()

        module = SimpleNamespace(PyAudio=SlowClosePortAudio, paWASAPI=13,
                                 paFloat32=1, paContinue=0)
        state, jobs = PipelineState(), Queue(4)
        state.update(mode=Mode.TRANSLATION)
        backend = SimpleNamespace(load=lambda: "Fake ASR", recognize=lambda job: "नमस्ते",
                                  close=lambda: None)
        translator = SimpleNamespace(translate=lambda *args: "Hello.", close=lambda: None)
        speech = SpeechWorker(AppConfig(), state, jobs, backend=backend, translator=translator)
        capture = DictationCaptureWorker(AppConfig(), state, jobs)
        order, pending = [], {"value": True}

        def recording_finished(epoch, submitted, message):
            order.append("capture")
            pending["value"] = submitted

        def dictation_done(epoch, message):
            order.append("translated")
            pending["value"] = False

        capture.finished_recording.connect(recording_finished)
        speech.dictation_done.connect(dictation_done)
        with patch.dict("sys.modules", {"pyaudiowpatch": module}):
            try:
                speech.start()
                capture.start()
                wait_for(lambda: state.snapshot().ready)
                epoch = state.begin_recording().epoch
                capture.request_start()
                wait_for(lambda: bool(SlowClosePortAudio.streams))
                stream = SlowClosePortAudio.streams[0]
                samples = tone(.6, 44100)
                stream.settings["stream_callback"](samples.tobytes(), len(samples), {}, 0)
                state.finish_recording(epoch)
                wait_for(cleanup_entered.is_set)
                self.assertTrue(jobs.empty(), "Audio must not reach inference during capture cleanup")
                self.assertEqual(order, [])
                release_cleanup.set()
                wait_for(lambda: "translated" in order)
                self.assertEqual(order, ["capture", "translated"])
                self.assertFalse(pending["value"])
                self.assertTrue(stream.closed)
            finally:
                release_cleanup.set()
                capture.request_stop()
                speech.request_stop()
                self.assertTrue(capture.wait(4000))
                self.assertTrue(speech.wait(4000))

    def test_failed_dictation_backend_load_is_closed_and_retried_fresh(self):
        config = AppConfig(dictation_backend="groq", dictation_model="whisper-large-v3")
        worker = SpeechWorker(config, PipelineState(), Queue(4))
        broken = SimpleNamespace(load=Mock(side_effect=RuntimeError("first load failed")), close=Mock())
        healthy = SimpleNamespace(load=Mock(return_value="Ready"), close=Mock())
        with patch.object(worker, "_make_backend", side_effect=[broken, healthy]) as factory:
            with self.assertRaisesRegex(RuntimeError, "first load failed"):
                worker._for_mode(Mode.TRANSLATION)
            broken.close.assert_called_once_with()
            self.assertIsNone(worker._dictation_backend)
            self.assertIs(worker._for_mode(Mode.TRANSLATION), healthy)
            self.assertIs(worker._for_mode(Mode.TRANSLATION), healthy)
            self.assertEqual(factory.call_count, 2)
            healthy.load.assert_called_once_with()
        worker._translator.close()
        healthy.close()

    def test_local_model_is_released_before_ollama_translation(self):
        state, jobs = PipelineState(), Queue(4)
        state.update(mode=Mode.TRANSLATION)
        config = AppConfig()
        backend = LocalWhisperBackend(config, lambda notice: None)
        retained_model = object()
        order, results, completed = [], [], []

        def recognize(job):
            self.assertIs(backend.model, retained_model)
            order.append("recognize")
            return "मैं कल आऊँगा।"

        def translate(text, epoch):
            self.assertIsNone(backend.model, "ASR model must release GPU memory before Ollama runs")
            self.assertEqual(text, "मैं कल आऊँगा।")
            order.append("translate")
            return "I will come tomorrow."

        translator = SimpleNamespace(translate=translate, close=Mock())
        worker = SpeechWorker(config, state, jobs, backend=backend, translator=translator)
        worker.result.connect(results.append)
        worker.dictation_done.connect(lambda *args: completed.append(args))
        with patch.object(backend, "load", side_effect=lambda: setattr(backend, "model", retained_model) or "Fake local"), \
                patch.object(backend, "recognize", side_effect=recognize):
            try:
                worker.start()
                wait_for(lambda: state.snapshot().ready)
                now = time.monotonic()
                jobs.put(AudioChunk(tone(.5), Source.MIC, Mode.TRANSLATION,
                                    state.snapshot().epoch, now, now))
                wait_for(lambda: bool(completed))
                self.assertEqual(order, ["recognize", "translate"])
                self.assertEqual([result.text for result in results], ["I will come tomorrow."])
            finally:
                worker.request_stop()
                self.assertTrue(worker.wait(4000))
        translator.close.assert_called_once_with()


class ReviewGeometryRegressions(unittest.TestCase):
    def test_expanded_translation_stays_inside_available_screen(self):
        self._assert_expansion_visible(settings=False)

    def test_expanded_settings_stays_inside_available_screen(self):
        self._assert_expansion_visible(settings=True)

    def _assert_expansion_visible(self, settings):
        window = Overlay(guard=FakeGuard())
        self.addCleanup(window.prepare_shutdown)
        window.show()
        APP.processEvents()
        area = window.screen().availableGeometry()
        if area.width() < 520 or area.height() < 600:
            self.skipTest("Test requires room for the default HUD and expanded settings")
        window.move(area.right() - window.width() + 1, area.bottom() - window.height() + 1)
        if settings:
            window.toggle_settings()
        else:
            window.select_mode(Mode.TRANSLATION)
        APP.processEvents()
        self.assertTrue(area.contains(window.frameGeometry()),
                        f"Expanded HUD {window.frameGeometry().getRect()} exceeds {area.getRect()}")


if __name__ == "__main__":
    unittest.main()
