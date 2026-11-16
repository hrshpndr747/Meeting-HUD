import os
from queue import Queue
import time
import unittest
from unittest.mock import patch
import numpy as np
from config import AppConfig
from pipeline import AudioChunk, Mode, PipelineState, Source, drain, expired, put_latest


class PipelineTests(unittest.TestCase):
    def test_control_epoch_rejects_stale_and_wrong_source_audio(self):
        state = PipelineState()
        self.assertFalse(state.snapshot().accepts(Source.MIC))
        ready = state.update(ready=True)
        self.assertTrue(state.accepts(ready.epoch, Source.SYSTEM))
        self.assertEqual(state.update(ready=True).epoch, ready.epoch)
        translation = state.update(mode=Mode.TRANSLATION)
        self.assertFalse(state.accepts(ready.epoch, Source.MIC))
        self.assertTrue(translation.accepts(Source.MIC))
        self.assertFalse(translation.accepts(Source.SYSTEM))
        self.assertFalse(state.update(paused=True).accepts(Source.MIC))
        self.assertFalse(state.update(stopped=True, paused=False).accepts(Source.MIC))

    def test_full_queue_preserves_newest_audio(self):
        queue = Queue(maxsize=2)
        self.assertFalse(put_latest(queue, 1))
        self.assertFalse(put_latest(queue, 2))
        self.assertTrue(put_latest(queue, 3))
        self.assertEqual([queue.get_nowait(), queue.get_nowait()], [2, 3])
        put_latest(queue, 4)
        drain(queue)
        self.assertTrue(queue.empty())

    def test_old_queued_audio_expires(self):
        chunk = AudioChunk(np.zeros(16000, dtype=np.float32), Source.MIC, Mode.TRANSCRIPT,
                           1, time.monotonic() - 9, time.monotonic() - 8)
        self.assertTrue(expired(chunk, 5))
        self.assertFalse(expired(chunk, 20))

    def test_defaults_and_environment_validation(self):
        with patch.dict(os.environ, {}, clear=True):
            config = AppConfig.from_env()
        self.assertEqual((config.model, config.translation_language, config.device), ("small", "hi", "auto"))
        for values in ({"HUD_DICTATION_MODEL": "small.en"}, {"HUD_MODEL": ""},
                       {"HUD_PAUSE_MS": "100"}, {"HUD_BACKEND": "groq"}, {"HUD_MIN_CHUNK_MS": "200"}):
            with self.subTest(values=values), patch.dict(os.environ, values, clear=True):
                with self.assertRaises(ValueError):
                    AppConfig.from_env()

    def test_optional_glossary_is_normalized_and_not_in_repr(self):
        with patch.dict(os.environ, {"HUD_INITIAL_PROMPT": "  Project  Lotus,\n Rohan. ",
                                    "GROQ_API_KEY": "sensitive-key"}, clear=True):
            config = AppConfig.from_env()
        self.assertEqual(config.initial_prompt, "Project Lotus, Rohan.")
        self.assertNotIn("Lotus", repr(config))
        self.assertNotIn("sensitive-key", repr(config))
        self.assertEqual(AppConfig().initial_prompt, "")
        with self.assertRaises(ValueError):
            AppConfig(initial_prompt="x" * 201)
        with self.assertRaises(ValueError):
            AppConfig(initial_prompt="term\x00term")


if __name__ == "__main__":
    unittest.main()
