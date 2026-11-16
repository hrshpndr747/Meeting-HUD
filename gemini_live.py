"""Bounded, source-separated continuous Gemini ASR over WebSockets in one QThread."""
from __future__ import annotations
import asyncio
import base64
from dataclasses import dataclass
import json
import logging
from queue import Empty, Queue
from threading import Event, Lock
import time
from urllib.parse import urlencode
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal
from audio_dsp import AudioProcessor, VadOutput
from gemini_protocol import input_transcripts, live_setup
from network_io import provider_error
from pipeline import Mode, Source, SpeechResult, drain, put_latest


@dataclass(frozen=True)
class LiveFrame:
    epoch: int
    serial: int
    sequence: int
    pcm: bytes
    captured_at: float


@dataclass(frozen=True)
class LivePartial:
    epoch: int
    source: Source
    text: str


class LiveAudioBus:
    def __init__(self):
        self.queues = {source: Queue(maxsize=20) for source in Source}
        self.lock = Lock()
        self.identities, self.sequences = {}, {}
        self.serial = 0

    def open(self, source, epoch):
        with self.lock:
            self.serial += 1
            identity = (epoch, self.serial)
            self.identities[source] = identity
            self.sequences[source] = 0
            drain(self.queues[source])
            return identity

    def close(self, source):
        with self.lock:
            self.identities.pop(source, None)
            drain(self.queues[source])

    def current(self, source):
        with self.lock:
            return self.identities.get(source)

    def publish(self, source, identity, samples, at):
        pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
        with self.lock:
            if self.identities.get(source) != identity:
                return
            self.sequences[source] += 1
            put_latest(self.queues[source], LiveFrame(*identity, self.sequences[source], pcm, at))

    def discard_stale_prefix(self, source, identity, max_age=1.5):
        """A slow handshake must not create an endless stale-frame reconnect loop."""
        with self.lock:
            if self.identities.get(source) != identity:
                return False
            queue, fresh, dropped = self.queues[source], [], False
            now = time.monotonic()
            while not queue.empty():
                frame = queue.get_nowait()
                if (frame.epoch, frame.serial) != identity or now - frame.captured_at > max_age:
                    dropped = True
                else:
                    fresh.append(frame)
            for frame in fresh:
                queue.put_nowait(frame)
            return dropped


class ContinuousProcessor(AudioProcessor):
    """No local speech gate: send 100 ms mono PCM, including natural pauses."""
    def __init__(self, rate, channels, noise_suppression=False):
        super().__init__(rate, channels, noise_suppression=noise_suppression)
        self.pending = np.empty(0, dtype=np.float32)

    def _process(self, data, captured_at):
        self.pending = np.concatenate((self.pending, self.resampler.feed(data)))
        parts = []
        while len(self.pending) >= 1600:
            audio = np.ascontiguousarray(self.pending[:1600])
            self.pending = self.pending[1600:]
            parts.append(VadOutput(audio, captured_at - len(self.pending) / 16000, False))
        return parts


class LiveFailure(RuntimeError):
    def __init__(self, message, permanent=False):
        super().__init__(message)
        self.permanent = permanent


class GeminiLiveWorker(QThread):
    result = pyqtSignal(object)
    partial = pyqtSignal(object)
    status = pyqtSignal(str)
    fault = pyqtSignal(str)
    connection = pyqtSignal(str, bool)

    def __init__(self, config, state, bus, connector=None):
        super().__init__()
        self.setObjectName("gemini-live-transcription")
        self.config, self.state, self.bus = config, state, bus
        self.stop_event = Event()
        self.connector = connector
        # Keys are in the WS query as documented. Never give protocol frames,
        # handshake URLs, close reasons or audio to application logging handlers.
        self.ws_logger = logging.Logger("meeting-hud-private-websocket", level=logging.CRITICAL + 1)
        self.ws_logger.addHandler(logging.NullHandler())
        self.ws_logger.propagate = False

    def request_stop(self):
        self.stop_event.set()

    def _current(self, source, identity):
        snap = self.state.snapshot()
        return (not self.stop_event.is_set() and snap.mode == Mode.TRANSCRIPT
                and snap.epoch == identity[0] and snap.accepts(source)
                and self.bus.current(source) == identity)

    async def _send(self, ws, source, identity):
        previous = None
        queue = self.bus.queues[source]
        while self._current(source, identity):
            try:
                frame = queue.get_nowait()
            except Empty:
                await asyncio.sleep(.02)
                continue
            if (frame.epoch, frame.serial) != identity:
                continue
            if time.monotonic() - frame.captured_at > 2 or (previous is not None and frame.sequence != previous + 1):
                raise LiveFailure("Audio/network backlog; restarting this caption stream. A short gap may be missing.")
            previous = frame.sequence
            if not self._current(source, identity):
                return
            await asyncio.wait_for(ws.send(json.dumps({"realtimeInput": {"audio": {
                "data": base64.b64encode(frame.pcm).decode("ascii"),
                "mimeType": "audio/pcm;rate=16000"}}})), timeout=2)

    async def _receive(self, ws, source, identity):
        while self._current(source, identity):
            raw = await ws.recv()
            if len(raw) > 262144:
                raise LiveFailure("Gemini streaming response exceeded its limit.")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise LiveFailure("Gemini returned invalid streaming data.")
            if "error" in data:
                error = data.get("error", {})
                code = error.get("code", 400) if isinstance(error, dict) else 400
                code = code if isinstance(code, int) else 400
                raise LiveFailure(provider_error("Gemini", code, data, (self.config.gemini_api_key,)),
                                  permanent=code in (400, 401, 403, 404, 429))
            for text, final in input_transcripts(data):
                if not self._current(source, identity):
                    return
                if final:
                    self.partial.emit(LivePartial(identity[0], source, ""))
                    self.result.emit(SpeechResult(text, source, Mode.TRANSCRIPT, identity[0], 0, 0, 0))
                else:
                    self.partial.emit(LivePartial(identity[0], source, text))
            # Do not use turnComplete as an input-transcript boundary: final input
            # transcription can arrive later. goAway requires a new connection.
            if "goAway" in data:
                raise LiveFailure("Gemini requested a new session; reconnecting. A short caption gap is possible.")

    async def _session(self, source, identity):
        if self.connector is None:
            from websockets.asyncio.client import connect
            connector = connect
        else:
            connector = self.connector
        url = ("wss://generativelanguage.googleapis.com/ws/"
               "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?" +
               urlencode({"key": self.config.gemini_api_key}))
        async with connector(url, open_timeout=8, close_timeout=1, ping_interval=20, ping_timeout=15,
                             max_size=262144, max_queue=8, compression=None, proxy=None,
                             logger=self.ws_logger) as ws:
            if not self._current(source, identity):
                return
            await ws.send(json.dumps(live_setup(self.config)))
            raw = await asyncio.wait_for(ws.recv(), timeout=8)
            data = json.loads(raw)
            if not isinstance(data, dict) or not isinstance(data.get("setupComplete"), dict):
                code = data.get("error", {}).get("code", 400) if isinstance(data, dict) else 400
                code = code if isinstance(code, int) else 400
                raise LiveFailure(provider_error("Gemini", code, data, (self.config.gemini_api_key,)), True)
            if not self._current(source, identity):
                return
            if self.bus.discard_stale_prefix(source, identity):
                self.status.emit("Gemini connection took time to open; older audio was skipped to resume live captions.")
            self.connection.emit(source.value, True)
            self.status.emit(f"Gemini {source.value} captions connected.")
            tasks = [asyncio.create_task(self._send(ws, source, identity)),
                     asyncio.create_task(self._receive(ws, source, identity))]
            try:
                done, _ = await asyncio.wait(tasks, timeout=540, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
                if not done:
                    raise LiveFailure("Renewing Gemini's live session. A short caption gap is possible.")
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_async(self):
        tasks, retries, delays = {}, {}, {s: .5 for s in Source}
        try:
            while not self.stop_event.is_set():
                now = time.monotonic()
                for source in Source:
                    active = tasks.get(source)
                    if active:
                        identity, task = active
                        if not self._current(source, identity) and not task.done():
                            task.cancel()
                        if not task.done():
                            continue
                        tasks.pop(source)
                        self.connection.emit(source.value, False)
                        self.partial.emit(LivePartial(identity[0], source, ""))
                        try:
                            task.result()
                        except asyncio.CancelledError:
                            pass
                        except Exception as exc:
                            if not self._current(source, identity):
                                # A queued auth/quota failure belongs to the old
                                # stream, not the mode the user has just selected.
                                retries.pop(source, None)
                                delays[source] = .5
                                continue
                            permanent = isinstance(exc, LiveFailure) and exc.permanent
                            # Handshake failures may embed URLs/keys; extract only status.
                            response = getattr(exc, "response", None)
                            code = getattr(response, "status_code", None)
                            close_code = getattr(getattr(exc, "rcvd", None), "code", None)
                            if close_code == 1008:
                                code = 403
                            if code in (400, 401, 403, 404, 429):
                                permanent = True
                            message = (str(exc) if isinstance(exc, LiveFailure) else
                                       provider_error("Gemini", code) if isinstance(code, int) else
                                       "Gemini live connection interrupted; reconnecting. A short caption gap may be missing.")
                            if permanent:
                                self.fault.emit(message)
                                return
                            if self._current(source, identity):
                                self.status.emit(message)
                                drain(self.bus.queues[source])
                                retries[source] = now + delays[source]
                                delays[source] = min(10, delays[source] * 2)
                        if not self._current(source, identity):
                            retries.pop(source, None)
                            delays[source] = .5
                    identity = self.bus.current(source)
                    if identity and self._current(source, identity) and now >= retries.get(source, 0):
                        tasks[source] = (identity, asyncio.create_task(self._session(source, identity)))
                await asyncio.sleep(.02)
        finally:
            for _, task in tasks.values():
                task.cancel()
            await asyncio.gather(*(task for _, task in tasks.values()), return_exceptions=True)

    def run(self):
        if self.config.backend != "gemini":
            return
        if not self.config.cloud_enabled or not self.config.gemini_api_key:
            self.fault.emit("Gemini live captions require a key and cloud permission. Open Settings or choose Local Whisper.")
            return
        try:
            asyncio.run(self._run_async())
        except Exception:
            self.fault.emit("Gemini live worker stopped. Check dependencies and model settings, then restart.")
