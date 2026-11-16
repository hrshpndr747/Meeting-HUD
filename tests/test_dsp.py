import unittest
import numpy as np
from scipy.signal import resample_poly
from audio_dsp import AdaptiveVAD, AudioProcessor, StreamingResampler


def tone(seconds, rate=16000, frequency=440, amplitude=0.2):
    return (amplitude * np.sin(2 * np.pi * frequency * np.arange(round(seconds * rate)) / rate)).astype("<f4")


class ResamplingTests(unittest.TestCase):
    def test_irregular_callback_boundaries_match_continuous_resampling(self):
        for rate in (8000, 16000, 44100, 48000, 96000):
            with self.subTest(rate=rate):
                signal = tone(0.97, rate)
                whole = StreamingResampler(rate, 1).feed(signal.tobytes())
                stream = StreamingResampler(rate, 1)
                pieces = []
                cursor = 0
                for size in (17, 63, 1001, 1, 212, 3007) * 20:
                    block = signal[cursor:cursor + size]
                    if not len(block):
                        break
                    pieces.append(stream.feed(block.tobytes()))
                    cursor += size
                if cursor < len(signal):
                    pieces.append(stream.feed(signal[cursor:].tobytes()))
                result = np.concatenate(pieces)
                np.testing.assert_array_equal(result, whole)
                reference = resample_poly(signal, stream.up, stream.down)
                np.testing.assert_allclose(result[20:-20], reference[20:len(result)-20], atol=2e-6)
                self.assertEqual(result.dtype, np.float32)
                self.assertTrue(result.flags.c_contiguous)
                self.assertLessEqual(len(stream._pending), 2 * stream.hop)

    def test_downmix_sanitizes_nonfinite_and_clips(self):
        stereo = np.full((1600, 2), (0.8, -0.4), dtype="<f4")
        stereo[400] = (float("nan"), float("inf"))
        output = StreamingResampler(16000, 2).feed(stereo.tobytes())
        self.assertTrue(np.isfinite(output).all())
        self.assertLessEqual(float(np.max(np.abs(output))), 1)
        self.assertAlmostEqual(float(np.median(output)), 0.2, places=6)

    def test_downsampling_rejects_above_nyquist_audio(self):
        output = StreamingResampler(48000, 1).feed(tone(1, 48000, 12000, 0.5).tobytes())
        rms = float(np.sqrt(np.mean(output[100:-100] ** 2)))
        self.assertLess(rms, 0.003)

    def test_invalid_interleaving_is_rejected(self):
        with self.assertRaises(ValueError):
            StreamingResampler(48000, 2).feed(np.zeros(3, dtype="<f4").tobytes())
        with self.assertRaises(ValueError):
            StreamingResampler(4000, 1)


class EndpointingTests(unittest.TestCase):
    def test_silence_does_not_create_chunks_or_unbounded_buffers(self):
        vad = AdaptiveVAD()
        for second in range(30):
            self.assertEqual(vad.feed(np.zeros(16000, dtype=np.float32), second + 1), [])
        self.assertEqual(len(vad._active), 0)
        self.assertLessEqual(len(vad._pre), 8)
        self.assertLess(len(vad._pending), 320)

    def test_short_phrase_is_retained_with_minimum_chunk_duration(self):
        audio = np.concatenate((np.zeros(3200, dtype=np.float32), tone(0.22), np.zeros(16000, dtype=np.float32)))
        chunks = AdaptiveVAD().feed(audio, len(audio) / 16000)
        self.assertEqual(len(chunks), 1)
        self.assertGreaterEqual(len(chunks[0].audio), 12800)
        self.assertAlmostEqual(chunks[0].speech_end, 0.42, places=5)
        self.assertFalse(chunks[0].overlaps_previous)
        self.assertFalse(chunks[0].audio.flags.writeable)

    def test_long_speech_has_bounded_chunks_and_marked_overlap(self):
        chunks = AdaptiveVAD().feed(tone(7), 7)
        self.assertEqual(len(chunks), 2)
        self.assertEqual([len(item.audio) for item in chunks], [48000, 48000])
        self.assertFalse(chunks[0].overlaps_previous)
        self.assertTrue(chunks[1].overlaps_previous)
        np.testing.assert_array_equal(chunks[0].audio[-1920:], chunks[1].audio[:1920])

    def test_isolated_click_is_not_a_phrase(self):
        audio = np.concatenate((np.zeros(3200, dtype=np.float32), tone(0.02), np.zeros(16000, dtype=np.float32)))
        self.assertEqual(AdaptiveVAD().feed(audio, len(audio) / 16000), [])

    def test_two_phrases_are_separate_without_overlap(self):
        audio = np.concatenate((tone(0.7), np.zeros(8000, dtype=np.float32), tone(0.7), np.zeros(8000, dtype=np.float32)))
        chunks = AdaptiveVAD().feed(audio, len(audio) / 16000)
        self.assertEqual(len(chunks), 2)
        self.assertTrue(all(not item.overlaps_previous for item in chunks))

    def test_loopback_tail_flushes_when_silent_callbacks_stop(self):
        processor = AudioProcessor(48000, 1)
        chunks = processor.feed(tone(0.6, 48000).tobytes(), 100.6)
        for time in np.arange(100.7, 102, 0.05):
            chunks.extend(processor.tick(float(time)))
        self.assertEqual(len(chunks), 1)
        self.assertGreaterEqual(len(chunks[0].audio), 12800)
        self.assertLess(chunks[0].speech_end, 100.7)


if __name__ == "__main__":
    unittest.main()
