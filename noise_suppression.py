"""Optional RNNoise native processing. Never imported/loaded in audio callbacks."""
from __future__ import annotations
import ctypes
from pathlib import Path
import sys
from threading import Lock
import numpy as np
from audio_dsp import StreamingResampler

_LIBRARIES = {}
_LIBRARY_LOCK = Lock()


def _library(path):
    # ctypes doesn't promise deterministic FreeLibrary. Load once per DLL path;
    # each stream still owns and explicitly destroys its own RNNoise state.
    key = str(path)
    with _LIBRARY_LOCK:
        if key not in _LIBRARIES:
            _LIBRARIES[key] = ctypes.CDLL(key)
        return _LIBRARIES[key]


class NoiseError(RuntimeError):
    pass


class RNNoiseFrames:
    """One native state, 480 float samples in signed-16-bit amplitude units."""
    def __init__(self, library_path=None):
        self.state = None
        try:
            name = "rnnoise.dll" if sys.platform == "win32" else "librnnoise.so"
            path = Path(library_path or Path(__file__).resolve().parent / "native" / name).resolve()
            self.lib = _library(path)
            self.lib.rnnoise_get_frame_size.argtypes = []
            self.lib.rnnoise_get_frame_size.restype = ctypes.c_int
            self.lib.rnnoise_create.argtypes = [ctypes.c_void_p]
            self.lib.rnnoise_create.restype = ctypes.c_void_p
            self.lib.rnnoise_destroy.argtypes = [ctypes.c_void_p]
            self.lib.rnnoise_destroy.restype = None
            self.lib.rnnoise_process_frame.argtypes = [ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float)]
            self.lib.rnnoise_process_frame.restype = ctypes.c_float
            if self.lib.rnnoise_get_frame_size() != 480:
                raise ValueError("Unexpected RNNoise frame size")
            self.state = self.lib.rnnoise_create(None)
            if not self.state:
                raise MemoryError()
        except Exception:
            self.close()
            raise NoiseError("Noise suppression unavailable. Run setup_noise.bat or disable it in Settings.") from None

    def process(self, samples):
        if self.state is None or len(samples) != 480:
            raise NoiseError("Invalid RNNoise state or frame.")
        frame = np.ascontiguousarray(np.clip(samples, -1, 1) * 32768, dtype=np.float32)
        ptr = frame.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
        self.lib.rnnoise_process_frame(self.state, ptr, ptr)
        return np.clip(frame / 32768, -1, 1)

    def close(self):
        if self.state is not None:
            self.lib.rnnoise_destroy(self.state)
            self.state = None

    def __del__(self):
        self.close()


class DenoisingResampler:
    """Native-rate mono -> 48 kHz RNNoise -> 16 kHz, preserving stop tails."""
    def __init__(self, rate, channels, denoiser=None):
        self.up = StreamingResampler(rate, channels, 48000)
        self.down = StreamingResampler(48000, 1)
        self.hop = self.up.hop
        self.denoiser = denoiser or RNNoiseFrames()
        self.pending = np.empty(0, dtype=np.float32)
        self.skip = 480  # RNNoise has one analysis frame of algorithmic delay.
        self.input_count = self.output_count = 0
        self.closed = False

    def _filter(self, samples):
        self.pending = np.concatenate((self.pending, samples))
        parts = []
        while len(self.pending) >= 480:
            out = self.denoiser.process(self.pending[:480])
            self.pending = self.pending[480:]
            skipped = min(self.skip, len(out))
            self.skip -= skipped
            if len(out) > skipped:
                parts.append(out[skipped:])
        joined = np.concatenate(parts) if parts else np.empty(0, dtype=np.float32)
        return self.down.feed(joined.astype("<f4", copy=False).tobytes())

    def feed(self, data):
        if self.closed:
            raise NoiseError("Noise processor is closed.")
        samples = self.up.feed(data)
        self.input_count += len(samples)
        out = self._filter(samples)
        self.output_count += len(out)
        return out

    def flush(self):
        if self.closed:
            return np.empty(0, dtype=np.float32)
        tail = self.up.flush()
        self.input_count += len(tail)
        remaining = len(self.pending) + len(tail)
        padding = 480 + (-remaining % 480)
        out = self._filter(np.concatenate((tail, np.zeros(padding, dtype=np.float32))))
        out = np.concatenate((out, self.down.flush()))
        needed = max(0, (self.input_count + 2) // 3 - self.output_count)
        self.close()
        return np.ascontiguousarray(out[:needed], dtype=np.float32)

    def close(self):
        if not self.closed:
            self.denoiser.close()
            self.closed = True


def make_resampler(rate, channels, enabled=False):
    return DenoisingResampler(rate, channels) if enabled else StreamingResampler(rate, channels)
