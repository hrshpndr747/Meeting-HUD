"""Explicit utterance recording. Pauses never submit or split a recording."""
from __future__ import annotations
from queue import Empty, Full, Queue
from threading import Event
import time
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal
from noise_suppression import NoiseError, make_resampler
from microphones import MIC_LEASE, MicrophoneError, resolve_microphone
from config import AppConfig
from pipeline import AudioChunk, Mode, PipelineState, SAMPLE_RATE, Source


class UtteranceBuffer:
    def __init__(self, rate: int, channels: int, maximum_s: float, floor: float, noise=False):
        self.resampler = make_resampler(rate, channels, noise)
        self.limit = round(maximum_s * SAMPLE_RATE)
        self.floor = floor
        self.parts = []
        self.count = self.voiced_samples = 0
        self.speech_end = 0.0

    def close(self):
        if hasattr(self.resampler, "close"):
            self.resampler.close()

    def _append(self, samples, captured_at):
        samples = samples[:self.limit - self.count]
        if not len(samples):
            return 0.0
        self.parts.append(samples)
        self.count += len(samples)
        centered = samples - samples.mean()
        rms = float(np.sqrt(np.mean(centered * centered) + 1e-12))
        if rms >= self.floor:
            self.voiced_samples += len(samples)
            self.speech_end = captured_at
        return min(1.0, rms * 12)

    def feed(self, data: bytes, captured_at: float) -> float:
        return self._append(self.resampler.feed(data), captured_at)

    def finish(self, captured_at: float) -> np.ndarray:
        self._append(self.resampler.flush(), captured_at)
        if self.count < 4000 or self.voiced_samples < 1920:
            return np.empty(0, dtype=np.float32)
        audio = np.ascontiguousarray(np.concatenate(self.parts), dtype=np.float32)
        audio.setflags(write=False)
        return audio


class DictationCaptureWorker(QThread):
    level = pyqtSignal(int, float)
    finished_recording = pyqtSignal(int, bool, str)

    def __init__(self, config: AppConfig, state: PipelineState, jobs: Queue):
        super().__init__()
        self.setObjectName("explicit-microphone-capture")
        self.config, self.state, self.jobs = config, state, jobs
        self._stop_event, self._wake = Event(), Event()

    def request_start(self) -> None:
        self._wake.set()

    def request_stop(self) -> None:
        self._stop_event.set()
        self._wake.set()

    def _current(self, epoch: int) -> bool:
        snapshot = self.state.snapshot()
        return (not self._stop_event.is_set() and snapshot.epoch == epoch
                and snapshot.mode == Mode.TRANSLATION and snapshot.accepts(Source.MIC))

    def _record(self, epoch: int) -> None:
        pa = stream = None
        mic_locked = False
        raw = Queue(maxsize=150)
        damaged = Event()
        buffer = None
        job = None
        last_frame = time.monotonic()
        message, submitted = "Recording cancelled.", False
        try:
            import pyaudiowpatch as pyaudio
            deadline = time.monotonic() + 3
            while self._current(epoch) and time.monotonic() < deadline:
                if MIC_LEASE.acquire(timeout=.05):
                    mic_locked = True
                    break
            if not mic_locked or not self._current(epoch):
                raise MicrophoneError("Microphone busy or recording cancelled. Wait and try again.")
            pa = pyaudio.PyAudio()
            device = resolve_microphone(pa, pyaudio.paWASAPI, self.config.microphone)
            index = int(device["index"])
            rate, channels = round(device["defaultSampleRate"]), int(device["maxInputChannels"])
            buffer = UtteranceBuffer(rate, channels, self.config.max_record_s, self.config.energy_floor,
                                     self.config.noise_suppression)

            def callback(data, frame_count, timing, status):
                snapshot = self.state.snapshot()
                if self._current(epoch) and snapshot.recording and data:
                    if status:
                        damaged.set()
                    try:
                        raw.put_nowait((data, time.monotonic()))
                    except Full:
                        damaged.set()
                return None, pyaudio.paContinue

            stream = pa.open(format=pyaudio.paFloat32, channels=channels, rate=rate,
                             input=True, input_device_index=index, frames_per_buffer=round(rate * .02),
                             stream_callback=callback)
            started, last_meter = time.monotonic(), 0.0
            last_frame = started
            while self._current(epoch) and self.state.snapshot().recording:
                if damaged.is_set():
                    raise OSError("Audio frames were lost")
                if not stream.is_active() or time.monotonic() - last_frame > 3:
                    raise OSError("Microphone disconnected")
                if time.monotonic() - started >= self.config.max_record_s or buffer.count >= buffer.limit:
                    self.state.finish_recording(epoch)
                    break
                try:
                    data, at = raw.get(timeout=.05)
                except Empty:
                    continue
                last_frame = at
                meter = buffer.feed(data, at)
                if at - last_meter >= .04:
                    self.level.emit(epoch, meter)
                    last_meter = at
            # Closing first quiesces callbacks; then retain frames queued before Stop.
            stream.close()
            stream = None
            while not raw.empty():
                data, at = raw.get_nowait()
                last_frame = at
                buffer.feed(data, at)
            if self._current(epoch):
                if damaged.is_set():
                    raise OSError("Audio frames were lost")
                audio = buffer.finish(last_frame)
                if len(audio):
                    job = AudioChunk(audio, Source.MIC, Mode.TRANSLATION, epoch,
                                     buffer.speech_end or last_frame, time.monotonic())
                    submitted, message = True, "Recognizing your words…"
                else:
                    message = "No clear speech captured. Press Record and try again."
        except Exception as exc:
            # Do not infer over incomplete recordings or make a failed microphone fatal to live captions.
            message = (str(exc) if isinstance(exc, (MicrophoneError, NoiseError)) else
                       f"Recording failed ({type(exc).__name__}). Check your microphone and record again.")
            job = None
        finally:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    message = "Microphone close failed. Reconnect the device before recording again."
                    job = None
            if pa is not None:
                try:
                    pa.terminate()
                except Exception:
                    message = "Audio driver cleanup failed. Restart the app before recording again."
                    job = None
            if buffer is not None:
                buffer.close()
            if mic_locked:
                MIC_LEASE.release()
            self.state.finish_recording(epoch)
            self.level.emit(epoch, 0.0)
            submitted = job is not None and self._current(epoch)
            self.finished_recording.emit(epoch, submitted, message)
            # Publish only after capture cleanup/completion. Fast inference must never
            # finish before a delayed recording_finished resurrects the pending state.
            if submitted:
                try:
                    self.jobs.put_nowait(job)
                except Full:
                    self.finished_recording.emit(epoch, False, "Speech queue is busy. Record again after it finishes.")

    def run(self) -> None:
        while not self._stop_event.is_set():
            self._wake.wait(.1)
            if not self._wake.is_set():
                continue
            self._wake.clear()
            snapshot = self.state.snapshot()
            if snapshot.recording and self._current(snapshot.epoch):
                self._record(snapshot.epoch)
