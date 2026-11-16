"""Stable WASAPI microphone selection and explicit local five-second mic checks."""
from __future__ import annotations
from dataclasses import dataclass
from queue import Empty, Full, Queue
from threading import Event, Lock
import time
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

MIC_LEASE = Lock()


class MicrophoneError(RuntimeError):
    pass


def identity(device):
    # Host type is fixed to WASAPI. Names are stable across index reordering;
    # duplicates deliberately fail instead of selecting the wrong microphone.
    return "wasapi:" + str(device["name"])


def microphones(pa, wasapi):
    host = pa.get_host_api_info_by_type(wasapi)
    devices = []
    for i in range(int(host["deviceCount"])):
        device = pa.get_device_info_by_host_api_device_index(int(host["index"]), i)
        if int(device.get("maxInputChannels", 0)) > 0 and not device.get("isLoopbackDevice", False):
            devices.append(device)
    return devices


def resolve_microphone(pa, wasapi, selected=""):
    if not selected:
        index = int(pa.get_host_api_info_by_type(wasapi)["defaultInputDevice"])
        if index < 0:
            raise MicrophoneError("No default WASAPI microphone. Select one in Windows Sound settings.")
        device = pa.get_device_info_by_index(index)
        if not int(device.get("maxInputChannels", 0)) or device.get("isLoopbackDevice", False):
            raise MicrophoneError("The default input is not a usable microphone.")
        return device
    found = [d for d in microphones(pa, wasapi) if identity(d) == selected]
    if len(found) != 1:
        raise MicrophoneError("Selected microphone is missing or has a duplicate name. Choose it again or rename it in Windows.")
    return found[0]


@dataclass(frozen=True)
class MicRequest:
    token: int
    selected: str = ""
    test: bool = False
    noise: bool = False


class MicWorker(QThread):
    devices = pyqtSignal(int, object, str)
    level = pyqtSignal(int, float)
    completed = pyqtSignal(int, str)

    def __init__(self):
        super().__init__()
        self.setObjectName("microphone-settings")
        self.jobs = Queue(maxsize=1)
        self.stop_event, self.cancel_event = Event(), Event()
        self.revision_lock = Lock()
        self.revision = self.active_revision = 0

    def submit(self, request):
        from pipeline import put_latest
        with self.revision_lock:
            self.revision += 1
            put_latest(self.jobs, (self.revision, request))

    def cancel(self):
        from pipeline import drain
        with self.revision_lock:
            self.revision += 1
            drain(self.jobs)

    def request_stop(self):
        self.stop_event.set()
        self.cancel()

    def _cancelled(self):
        with self.revision_lock:
            return self.stop_event.is_set() or self.active_revision != self.revision

    def _test(self, pa, api, request):
        from noise_suppression import make_resampler
        stream = processor = None
        locked = False
        raw, damaged = Queue(maxsize=100), Event()
        try:
            deadline = time.monotonic() + 3
            while not self._cancelled() and time.monotonic() < deadline:
                if MIC_LEASE.acquire(timeout=.05):
                    locked = True
                    break
            if not locked or self._cancelled():
                raise MicrophoneError("Microphone busy. Wait for recording to stop, then test again.")
            device = resolve_microphone(pa, api.paWASAPI, request.selected)
            rate, channels = round(device["defaultSampleRate"]), int(device["maxInputChannels"])
            processor = make_resampler(rate, channels, request.noise)

            def callback(data, frames, timing, flags):
                if data and not self._cancelled():
                    try:
                        raw.put_nowait(data)
                    except Full:
                        damaged.set()
                    if flags:
                        damaged.set()
                return None, api.paContinue

            stream = pa.open(format=api.paFloat32, channels=channels, rate=rate, input=True,
                             input_device_index=int(device["index"]), frames_per_buffer=round(rate * .02),
                             stream_callback=callback)
            end = time.monotonic() + 5
            total = clipped = 0
            peak_rms = 0.0
            while not self._cancelled() and time.monotonic() < end:
                if not stream.is_active() or damaged.is_set():
                    raise MicrophoneError("Microphone test interrupted. Reconnect the device and retry.")
                try:
                    data = raw.get(timeout=.05)
                except Empty:
                    continue
                native = np.frombuffer(data, dtype="<f4")
                total += len(native)
                clipped += int(np.count_nonzero(np.abs(native) >= .995))
                samples = processor.feed(data)
                if len(samples):
                    rms = float(np.sqrt(np.mean(samples * samples)))
                    peak_rms = max(peak_rms, rms)
                    self.level.emit(request.token, min(1, rms * 12))
            if self._cancelled():
                return
            message = ("No audio received. Check Windows microphone permissions." if not total else
                       "Input is clipping. Lower the microphone level or move it farther away." if clipped / total > .001 else
                       "Input is very quiet. Move closer or increase the microphone level." if peak_rms < .008 else
                       "Level looks usable. This checks level/clipping, not recognition accuracy.")
            self.completed.emit(request.token, message)
        finally:
            try:
                if stream is not None:
                    stream.close()
            finally:
                if processor is not None and hasattr(processor, "close"):
                    processor.close()
                if locked:
                    MIC_LEASE.release()

    def run(self):
        while not self.stop_event.is_set():
            try:
                revision, request = self.jobs.get(timeout=.1)
            except Empty:
                continue
            self.active_revision = revision
            if self.stop_event.is_set():
                break
            if self._cancelled():
                continue
            pa = None
            try:
                import pyaudiowpatch as api
                pa = api.PyAudio()
                if request.test:
                    self._test(pa, api, request)
                else:
                    rows = [(identity(d), str(d["name"])) for d in microphones(pa, api.paWASAPI)]
                    self.devices.emit(request.token, rows, "")
            except Exception as exc:
                from noise_suppression import NoiseError
                message = str(exc) if isinstance(exc, (MicrophoneError, NoiseError)) else "Microphone check failed. Check devices and Windows permissions."
                if request.test:
                    self.completed.emit(request.token, message)
                else:
                    self.devices.emit(request.token, [], message)
            finally:
                if pa is not None:
                    try:
                        pa.terminate()
                    except Exception:
                        self.completed.emit(request.token, "Audio cleanup failed; restart before using the microphone.")
