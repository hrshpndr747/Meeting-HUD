"""Streaming rational resampling and energy-based speech endpointing."""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass
from math import ceil, gcd
import numpy as np
from scipy.signal import resample_poly
from pipeline import SAMPLE_RATE


class StreamingResampler:
    """Polyphase-aligned hops with a full hop of history and lookahead."""
    def __init__(self, input_rate: int, channels: int, output_rate: int = SAMPLE_RATE) -> None:
        if not 8000 <= input_rate <= 192000 or not 1 <= channels <= 32 or not 8000 <= output_rate <= 48000:
            raise ValueError("Unsupported audio format.")
        self.channels = channels
        divisor = gcd(input_rate, output_rate)
        self.up, self.down = output_rate // divisor, input_rate // divisor
        self.hop = self.down * ceil(input_rate * 0.02 / self.down)
        self.output_hop = self.hop * self.up // self.down
        self._previous = np.zeros(self.hop, dtype=np.float32)
        self._pending = np.empty(0, dtype=np.float32)

    def feed(self, data: bytes) -> np.ndarray:
        samples = np.frombuffer(data, dtype="<f4")
        if len(samples) % self.channels:
            raise ValueError("Incomplete interleaved audio frame.")
        mono = samples.reshape(-1, self.channels).mean(axis=1, dtype=np.float32)
        mono = np.nan_to_num(mono, nan=0.0, posinf=0.0, neginf=0.0)
        self._pending = np.concatenate((self._pending, np.clip(mono, -1.0, 1.0)))
        output = []
        while len(self._pending) >= 2 * self.hop:
            current = self._pending[:self.hop]
            context = np.concatenate((self._previous, self._pending[:2 * self.hop]))
            filtered = resample_poly(context, self.up, self.down)
            output.append(filtered[self.output_hop:2 * self.output_hop])
            self._previous = current.copy()
            self._pending = self._pending[self.hop:]
        return (np.ascontiguousarray(np.concatenate(output), dtype=np.float32)
                if output else np.empty(0, dtype=np.float32))

    def flush(self) -> np.ndarray:
        """Finish an explicitly stopped utterance without losing its last hop."""
        if not len(self._pending):
            return np.empty(0, dtype=np.float32)
        count = ceil(len(self._pending) * self.up / self.down)
        context = np.concatenate((self._previous, self._pending,
                                  np.zeros(2 * self.hop, dtype=np.float32)))
        filtered = resample_poly(context, self.up, self.down)
        output = np.ascontiguousarray(filtered[self.output_hop:self.output_hop + count], dtype=np.float32)
        self._pending = np.empty(0, dtype=np.float32)
        self._previous.fill(0)
        return output


@dataclass(frozen=True)
class VadOutput:
    audio: np.ndarray
    speech_end: float
    overlaps_previous: bool


class AdaptiveVAD:
    """Fast acoustic endpointing; local inference applies Silero to both tasks."""
    FRAME = 320

    def __init__(self, min_s: float = 0.8, max_s: float = 3.0,
                 pause_s: float = 0.24, energy_floor: float = 0.003) -> None:
        self.min_frames = ceil(min_s / 0.02)
        self.max_frames = int(max_s / 0.02)
        self.pause_frames = ceil(pause_s / 0.02)
        self.floor = energy_floor
        self.noise = energy_floor / 3
        self._pending = np.empty(0, dtype=np.float32)
        self._pre: deque[np.ndarray] = deque(maxlen=8)
        self._active: list[np.ndarray] = []
        self._voices = self._silence = self._onset = 0
        self._overlap = False
        self._last_voice = 0.0

    def feed(self, samples: np.ndarray, end_time: float) -> list[VadOutput]:
        self._pending = np.concatenate((self._pending, samples))
        frame_time = end_time - len(self._pending) / SAMPLE_RATE
        result = []
        while len(self._pending) >= self.FRAME:
            frame = self._pending[:self.FRAME].copy()
            self._pending = self._pending[self.FRAME:]
            frame_time += 0.02
            centered = frame - frame.mean()
            rms = float(np.sqrt(np.mean(centered * centered) + 1e-12))
            voiced = rms >= max(self.floor, self.noise * 3.0)
            if not self._active and not voiced:
                self.noise = 0.98 * self.noise + 0.02 * min(rms, self.floor * 2)
            if voiced:
                self._last_voice = frame_time
            if not self._active:
                self._pre.append(frame)
                self._onset = self._onset + 1 if voiced else 0
                if self._onset >= 2:
                    self._active = list(self._pre)
                    self._voices = self._onset
                    self._silence = 0
                    self._pre.clear()
                continue
            self._active.append(frame)
            self._voices += int(voiced)
            self._silence = 0 if voiced else self._silence + 1
            natural = self._silence >= self.pause_frames and len(self._active) >= self.min_frames
            forced = len(self._active) >= self.max_frames
            if not natural and not forced:
                continue
            if self._voices >= 6:
                audio = np.ascontiguousarray(np.concatenate(self._active), dtype=np.float32)
                audio.setflags(write=False)
                result.append(VadOutput(audio, self._last_voice, self._overlap))
            tail = self._active[-6:] if forced and voiced else []
            self._active = list(tail)
            self._voices = self._silence = self._onset = 0
            self._overlap = bool(tail)
        return result


class AudioProcessor:
    """One source's DSP plus endpointing when loopback silent callbacks cease."""
    def __init__(self, rate: int, channels: int, min_s: float = 0.8,
                 max_s: float = 3.0, pause_s: float = 0.24, floor: float = 0.003,
                 noise_suppression: bool = False):
        self.rate, self.channels = rate, channels
        from noise_suppression import make_resampler
        self.resampler = make_resampler(rate, channels, noise_suppression)
        self.vad = AdaptiveVAD(min_s, max_s, pause_s, floor)
        self._last_input: float | None = None
        self._last_tick: float | None = None

    def close(self):
        close = getattr(self.resampler, "close", None)
        if close:
            close()

    def feed(self, data: bytes, captured_at: float) -> list[VadOutput]:
        self._last_input = self._last_tick = captured_at
        return self._process(data, captured_at)

    def _process(self, data: bytes, captured_at: float) -> list[VadOutput]:
        samples = self.resampler.feed(data)
        return (self.vad.feed(samples, captured_at - self.resampler.hop / self.rate)
                if len(samples) else [])

    def tick(self, now: float) -> list[VadOutput]:
        if self._last_input is None or now - self._last_input < 0.10:
            return []
        elapsed = now - self._last_tick
        if elapsed < 0.04:
            return []
        duration = min(0.10, elapsed)
        self._last_tick += duration
        data = np.zeros((round(self.rate * duration), self.channels), dtype="<f4").tobytes()
        return self._process(data, self._last_tick)
