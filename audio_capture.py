"""WASAPI callbacks enqueue bytes; DSP and stream management run in a QThread."""
from __future__ import annotations
from dataclasses import dataclass
import logging
from queue import Empty, Queue
from threading import Event, Lock
import time
from PyQt6.QtCore import QThread, pyqtSignal
from audio_dsp import AudioProcessor
from config import AppConfig
from pipeline import AudioChunk, Mode, PipelineState, Source, drain, put_latest
from win32_audio import EndpointWatcher
from microphones import MIC_LEASE, MicrophoneError, resolve_microphone

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RawFrame:
    source: Source
    epoch: int
    serial: int
    data: bytes
    captured_at: float
    discontinuity: bool


class AudioCaptureWorker(QThread):
    status = pyqtSignal(str)
    source_state = pyqtSignal(str, bool)
    fault = pyqtSignal(str)

    def __init__(self, config: AppConfig, state: PipelineState, chunks: Queue[AudioChunk], live_bus=None):
        super().__init__()
        self.setObjectName("wasapi-capture")
        self.config, self.state, self.chunks = config, state, chunks
        self.live_bus = live_bus
        self._stop_event = Event()
        self._raw: Queue[RawFrame] = Queue(maxsize=100)
        self._stats_lock = Lock()
        self._callback_losses = 0
        self._last_notice = 0.0

    def request_stop(self) -> None:
        self._stop_event.set()

    def _notice(self, text: str) -> None:
        if time.monotonic() - self._last_notice > 3:
            self.status.emit(text)
            self._last_notice = time.monotonic()

    def _callback(self, source: Source, epoch: int, serial: int, pa_continue: int):
        def callback(data, frame_count, time_info, status_flags):
            if (data and not self._stop_event.is_set() and self.state.accepts(epoch, source)
                    and self.state.snapshot().mode == Mode.TRANSCRIPT):
                lost = put_latest(self._raw, RawFrame(source, epoch, serial, data,
                                                      time.monotonic(), bool(status_flags)))
                if lost:
                    with self._stats_lock:
                        self._callback_losses += 1
            return None, pa_continue
        return callback

    def run(self) -> None:
        pa = watcher = None
        streams, processors, stream_serials = {}, {}, {}
        mic_locked = False
        live_identities = {}
        serial = seen_losses = 0
        retries = {s: 0.0 for s in Source}
        backoff = {s: 0.5 for s in Source}
        epoch = -1
        default_ids = None
        next_device_check = 0.0

        def close_stream(source):
            nonlocal mic_locked
            stream = streams.pop(source, None)
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    log.warning("Audio stream close failed for %s", source.value)
                self.source_state.emit(source.value, False)
            processor = processors.pop(source, None)
            if processor is not None:
                processor.close()
            if source == Source.MIC and mic_locked:
                MIC_LEASE.release()
                mic_locked = False
            if self.live_bus:
                self.live_bus.close(source)
            stream_serials.pop(source, None)

        def close_all():
            nonlocal pa
            for source in set(streams) | set(processors):
                close_stream(source)
            if pa is not None:
                try:
                    pa.terminate()
                except Exception:
                    log.warning("PortAudio termination failed")
                pa = None
            drain(self._raw)

        def new_dsp(rate, channels, source):
            previous = processors.get(source)
            if previous is not None:
                previous.close()
            noise = self.config.noise_suppression and source == Source.MIC
            if self.config.backend == "gemini" and self.live_bus:
                from gemini_live import ContinuousProcessor
                processor = ContinuousProcessor(rate, channels, noise)
                live_identities[source] = self.live_bus.open(source, epoch)
                return processor
            return AudioProcessor(rate, channels, self.config.min_chunk_s, self.config.max_chunk_s,
                                  self.config.pause_s, self.config.energy_floor, noise)

        def submit(parts, source, snapshot):
            for part in parts:
                if not self.state.accepts(snapshot.epoch, source):
                    break
                if self.config.backend == "gemini" and self.live_bus:
                    self.live_bus.publish(source, live_identities[source], part.audio, part.speech_end)
                    continue
                chunk = AudioChunk(part.audio, source, snapshot.mode, snapshot.epoch,
                                   part.speech_end, time.monotonic(), part.overlaps_previous)
                if put_latest(self.chunks, chunk):
                    self._notice("Inference is behind; oldest audio discarded to stay live.")

        try:
            import pyaudiowpatch as pyaudio
            try:
                watcher = EndpointWatcher()
                default_ids = watcher.defaults()
            except Exception:
                if watcher:
                    watcher.close()
                watcher = None
                self.status.emit("Device-change polling unavailable; failed streams still retry.")
            while not self._stop_event.is_set():
                snapshot = self.state.snapshot()
                if snapshot.stopped:
                    break
                now = time.monotonic()
                if snapshot.epoch != epoch:
                    close_all()
                    epoch = snapshot.epoch
                    retries = {s: 0.0 for s in Source}
                    backoff = {s: 0.5 for s in Source}
                wanted = [s for s in Source if snapshot.mode == Mode.TRANSCRIPT and snapshot.accepts(s)]
                if not wanted:
                    self._stop_event.wait(0.05)
                    continue
                if watcher and now >= next_device_check:
                    next_device_check = now + 1.5
                    try:
                        current_ids = watcher.defaults()
                        if current_ids != default_ids:
                            close_all()
                            default_ids = current_ids
                            retries = {s: 0.0 for s in Source}
                            self.status.emit("Audio device changed; reconnecting.")
                    except Exception:
                        self._notice("Unable to check default devices; stream recovery remains active.")
                if pa is None:
                    try:
                        pa = pyaudio.PyAudio()
                    except Exception:
                        self._notice("Windows audio is unavailable; retrying.")
                        self._stop_event.wait(1)
                        continue
                for source in wanted:
                    if source in streams:
                        try:
                            if not streams[source].is_active():
                                raise OSError("inactive stream")
                        except Exception:
                            close_stream(source)
                            close_all()
                            retries[source] = now + 0.5
                            break
                    if source in streams or now < retries[source]:
                        continue
                    try:
                        if source == Source.SYSTEM:
                            device = pa.get_default_wasapi_loopback()
                        else:
                            mic_locked = MIC_LEASE.acquire(blocking=False)
                            if not mic_locked:
                                raise MicrophoneError("Microphone busy; waiting for the local check to finish.")
                            device = resolve_microphone(pa, pyaudio.paWASAPI, self.config.microphone)
                        channels = int(device["maxInputChannels"])
                        rate = int(round(device["defaultSampleRate"]))
                        processors[source] = new_dsp(rate, channels, source)
                        serial += 1
                        stream_serials[source] = serial
                        streams[source] = pa.open(
                            format=pyaudio.paFloat32, channels=channels, rate=rate,
                            input=True, input_device_index=int(device["index"]),
                            frames_per_buffer=max(128, round(rate * 0.02)),
                            stream_callback=self._callback(source, epoch, serial, pyaudio.paContinue),
                        )
                        backoff[source] = 0.5
                        self.source_state.emit(source.value, True)
                        self.status.emit(f"{source.value.capitalize()} connected.")
                    except Exception as exc:
                        close_stream(source)
                        retries[source] = now + backoff[source]
                        backoff[source] = min(5, backoff[source] * 2)
                        self.source_state.emit(source.value, False)
                        from noise_suppression import NoiseError
                        self._notice(str(exc) if isinstance(exc, (MicrophoneError, NoiseError)) else
                                     f"{source.value.capitalize()} unavailable; retrying. Check Windows audio settings.")
                        log.debug("Audio open failed for %s: %s", source.value, type(exc).__name__)
                if pa is None:
                    continue
                with self._stats_lock:
                    losses = self._callback_losses
                if losses != seen_losses:
                    seen_losses = losses
                    drain(self._raw)
                    for source, processor in list(processors.items()):
                        processors[source] = new_dsp(processor.rate, processor.channels, source)
                    self._notice("Audio backlog discarded; live capture resumed.")
                if self._raw.empty():
                    for source, processor in processors.items():
                        submit(processor.tick(time.monotonic()), source, snapshot)
                try:
                    frame = self._raw.get(timeout=0.05)
                except Empty:
                    continue
                if (not self.state.accepts(frame.epoch, frame.source)
                        or frame.serial != stream_serials.get(frame.source)
                        or frame.source not in processors):
                    continue
                processor = processors[frame.source]
                if frame.discontinuity:
                    processors[frame.source] = new_dsp(processor.rate, processor.channels, frame.source)
                    processor = processors[frame.source]
                    self._notice("Audio discontinuity; restarting speech boundary.")
                submit(processor.feed(frame.data, frame.captured_at), frame.source, snapshot)
        except Exception as exc:
            log.error("Capture stopped: %s", type(exc).__name__)
            self.fault.emit(f"Audio capture stopped ({type(exc).__name__}). Check devices and restart.")
        finally:
            close_all()
            if watcher is not None:
                watcher.close()
