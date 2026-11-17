"""Explicit microphone streaming; stable ASR phrases become incremental English."""
from __future__ import annotations
import asyncio
import base64
from dataclasses import dataclass, field, replace
import json
import logging
from queue import Empty, Full, Queue
from threading import Event, Lock
import time
from urllib.parse import urlencode
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal
from gemini_protocol import input_transcripts, live_setup
from gemini_translation import GeminiTextStream, StreamCancelled, StreamError
from latency_metrics import metric
from network_io import provider_error
from pipeline import Mode, Source, RecognizedText, TranslationUpdate

@dataclass
class RecordingStream:
    epoch: int
    frames: Queue = field(default_factory=lambda: Queue(maxsize=50))
    ready: Event = field(default_factory=Event)
    capturing: Event = field(default_factory=Event)
    ended: Event = field(default_factory=Event)
    error: str = ''
    speech_end: float = 0.
    ended_at: float = 0.
    pending: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.float32))

    def _put(self, samples):
        try:
            self.frames.put_nowait((np.clip(samples, -1, 1)*32767).astype('<i2').tobytes())
        except Full:
            self.error = 'Audio upload could not keep up. Stop and retry on a stable connection; this recording is incomplete.'
            raise RuntimeError(self.error) from None

    def publish(self, samples, captured_at):
        self.pending = np.concatenate((self.pending, samples))
        while len(self.pending) >= 1600:
            self._put(self.pending[:1600])
            self.pending = self.pending[1600:]

    def finish(self, speech_end=0., error=''):
        try:
            if len(self.pending) and not self.error:
                self._put(self.pending)
            self.pending = np.empty(0, dtype=np.float32)
        except RuntimeError:
            pass
        finally:
            self.speech_end = speech_end
            self.error = self.error or error
            self.capturing.clear()
            self.ended_at = time.monotonic()
            self.ended.set()

class DictationStreamBus:
    def __init__(self):
        self._lock, self._session = Lock(), None
    def begin(self, epoch):
        with self._lock:
            self._session = RecordingStream(epoch)
            return self._session
    def current(self):
        with self._lock:
            return self._session

@dataclass
class Phrase:
    text: str = ''
    stable: str = ''
    final: bool = False
    revision: int = 0
    translated: int = -1
    english: str = ''
    changed_at: float = 0.
    requested_at: float = 0.
    source_at: float = 0.

class PhraseBook:
    def __init__(self):
        self.phrases = []
        self.last_final = 0.
        self.last_update = 0.

    def update(self, text, final, now=None):
        now = time.monotonic() if now is None else now
        if not self.phrases or self.phrases[-1].final:
            self.phrases.append(Phrase())
        phrase = self.phrases[-1]
        if text != phrase.text or final != phrase.final:
            old = phrase.text.split()
            new = text.split()
            prefix = []
            for a, b in zip(old, new):
                if a != b:
                    break
                prefix.append(b)
            phrase.stable = ' '.join(prefix)
            phrase.text, phrase.final = text, final
            phrase.revision += 1
            phrase.changed_at = phrase.source_at = now
        if final:
            self.last_final = now
        self.last_update = now
        if len(self.phrases) > 160 or len(self.source()) > 8000:
            raise StreamError('Recording exceeded the text limit. Stop and translate a shorter passage.')

    def source(self):
        return ' '.join(p.text for p in self.phrases if p.text)
    def english(self):
        return ' '.join(p.english for p in self.phrases if p.english)
    def complete(self):
        return bool(self.phrases) and all(p.final and p.translated == p.revision for p in self.phrases)
    def has_interim(self):
        return bool(self.phrases and not self.phrases[-1].final)
    def next(self, now=None):
        now = time.monotonic() if now is None else now
        for index, phrase in enumerate(self.phrases):
            if phrase.final and phrase.translated != phrase.revision:
                return index, phrase, phrase.text
        if not self.phrases:
            return None
        phrase = self.phrases[-1]
        if phrase.final or phrase.translated == phrase.revision or now-phrase.requested_at < .8:
            return None
        candidate = phrase.text if now-phrase.changed_at >= .3 else phrase.stable
        if len(candidate.split()) < 3 and now-phrase.changed_at < 1.0:
            return None
        if candidate.strip():
            return len(self.phrases)-1, phrase, candidate
        return None

class StreamingTranslationWorker(QThread):
    recognized = pyqtSignal(object)
    partial = pyqtSignal(object)
    completed = pyqtSignal(int, str)
    status = pyqtSignal(str)
    error = pyqtSignal(int, str)

    def __init__(self, config, state, bus, connector=None, translator_factory=None):
        super().__init__()
        self.setObjectName('streaming-english')
        self.config, self.state, self.bus = config, state, bus
        self.stop_event = Event()
        self.connector, self.translator_factory = connector, translator_factory
        self.ws_logger = logging.Logger('private-dictation-websocket', logging.CRITICAL+1)
        self.ws_logger.addHandler(logging.NullHandler())
        self.ws_logger.propagate = False

    def request_stop(self):
        self.stop_event.set()
    def _current(self, session):
        snap = self.state.snapshot()
        return (not self.stop_event.is_set() and snap.mode == Mode.TRANSLATION
                and self.state.accepts(session.epoch, Source.MIC) and self.bus.current() is session)

    async def _send(self, ws, session):
        while self._current(session):
            if session.error:
                raise StreamError(session.error)
            try:
                pcm = session.frames.get_nowait()
            except Empty:
                if session.ended.is_set():
                    await asyncio.wait_for(ws.send(json.dumps({'realtimeInput': {'audioStreamEnd': True}})), 2)
                    metric('audio_stream_ended', epoch=session.epoch)
                    return
                await asyncio.sleep(.02)
                continue
            await asyncio.wait_for(ws.send(json.dumps({'realtimeInput': {'audio': {
                'data': base64.b64encode(pcm).decode('ascii'), 'mimeType': 'audio/pcm;rate=16000'}}})), 2)

    async def _receive(self, ws, session, book):
        while self._current(session):
            raw = await ws.recv()
            if len(raw) > 262144:
                raise StreamError('Recognition response exceeded its limit.')
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise StreamError('Recognition returned invalid data.')
            if data.get('error'):
                code = data['error'].get('code', 400) if isinstance(data['error'], dict) else 400
                raise StreamError(provider_error('Gemini', code if isinstance(code, int) else 400))
            for text, final in input_transcripts(data):
                if not self._current(session):
                    return
                book.update(text, final)
                self.recognized.emit(RecognizedText(book.source(), session.epoch))
                metric('source_final' if final else 'source_interim', epoch=session.epoch,
                       segment=len(book.phrases)-1, revision=book.phrases[-1].revision, characters=len(text))
            # Input transcription is independent of turnComplete: never finalize on it.
            if 'goAway' in data:
                raise StreamError('Gemini requested a new connection. Source and draft English are preserved; retry.')

    async def _translate(self, session, book, translator, failures):
        while self._current(session):
            if failures:
                await asyncio.sleep(.03)
                continue
            item = book.next()
            if item is None:
                await asyncio.sleep(.03)
                continue
            index, phrase, source = item
            revision, is_final = phrase.revision, phrase.final
            phrase.requested_at = time.monotonic()
            def cancelled():
                return not self._current(session) or not (phrase.text == source or
                         (not phrase.final and phrase.text.startswith(source + ' ')))
            def partial(text):
                if not cancelled():
                    phrase.english = text.strip()
                    self.partial.emit(TranslationUpdate(book.english(), session.epoch))
            try:
                english = await translator.translate(source, cancelled, partial, epoch=session.epoch,
                    segment=index, revision=revision, final=is_final, source_at=phrase.source_at,
                    context=' '.join(p.text for p in book.phrases[max(0, index-2):index]))
                if not cancelled():
                    phrase.english = english
                    phrase.translated = phrase.revision if source == phrase.text else -1
                    self.partial.emit(TranslationUpdate(book.english(), session.epoch))
            except StreamCancelled:
                continue
            except StreamError as exc:
                failures.append(str(exc))
                if self._current(session):
                    self.error.emit(session.epoch, str(exc))

    async def _session(self, session, translator):
        if self.connector is None:
            from websockets.asyncio.client import connect
            connector = connect
        else:
            connector = self.connector
        url = ('wss://generativelanguage.googleapis.com/ws/'
               'google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?' +
               urlencode({'key': self.config.gemini_api_key}))
        book, failures, tasks = PhraseBook(), [], []
        async with connector(url, open_timeout=8, close_timeout=1, ping_interval=20, ping_timeout=15,
                             max_size=262144, max_queue=8, compression=None, proxy=None,
                             logger=self.ws_logger) as ws:
            config = replace(self.config, model=self.config.dictation_live_model,
                             transcript_language=self.config.translation_language)
            await asyncio.wait_for(ws.send(json.dumps(live_setup(config))), 3)
            data = json.loads(await asyncio.wait_for(ws.recv(), 8))
            if not isinstance(data, dict) or not isinstance(data.get('setupComplete'), dict):
                raise StreamError('Gemini recognition could not connect. Check your key, live model and access in Settings.')
            if not self._current(session):
                return ''
            session.ready.set()
            metric('recognition_connected', epoch=session.epoch, model=config.model)
            self.status.emit('Recognition connected · opening microphone…')
            send = asyncio.create_task(self._send(ws, session))
            receive = asyncio.create_task(self._receive(ws, session, book))
            translate = asyncio.create_task(self._translate(session, book, translator, failures))
            tasks = [send, receive, translate]
            drain_started = None
            try:
                while self._current(session):
                    for task in tasks:
                        if task.done():
                            task.result()
                            if task is not send:
                                raise StreamError('Translation connection ended unexpectedly. Review source and retry.')
                    if session.ended.is_set() and send.done():
                        now = time.monotonic()
                        finalized = (book.phrases and not book.has_interim() and
                                     book.last_final >= session.speech_end and
                                     now-max(book.last_update, session.ended_at) >= .75)
                        if finalized:
                            if drain_started is None:
                                drain_started = now
                            if failures:
                                return failures[0]
                            if book.complete():
                                self.partial.emit(TranslationUpdate(book.english(), session.epoch, True))
                                metric('recording_translation_completed', epoch=session.epoch,
                                       elapsed_ms=(now-session.ended_at)*1000)
                                return 'English ready · microphone off'
                            if now-drain_started > 35:
                                raise StreamError('Final English timed out. Review source and retry; displayed English is a draft.')
                        elif now-session.ended_at > 8:
                            raise StreamError('Recognition did not finalize in time. Source and draft English are preserved; retry.')
                    await asyncio.sleep(.03)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        return ''

    async def _run_async(self):
        translator = (self.translator_factory(self.config) if self.translator_factory else GeminiTextStream(self.config))
        seen = None
        try:
            while not self.stop_event.is_set():
                session = self.bus.current()
                if session is None or session is seen or not self._current(session):
                    await asyncio.sleep(.02)
                    continue
                seen = session
                task = asyncio.create_task(self._session(session, translator))
                message = ''
                try:
                    while not task.done() and self._current(session):
                        await asyncio.wait({task}, timeout=.03)
                    if not self._current(session):
                        task.cancel()
                    else:
                        message = task.result()
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    message = str(exc) if isinstance(exc, StreamError) else 'Streaming connection failed. Check internet/model settings; your source and draft English are preserved.'
                    session.error = message
                    if self._current(session):
                        self.error.emit(session.epoch, message)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    session.ready.set()  # Release capture if setup failed; error prevents microphone access.
                    self.state.finish_recording(session.epoch)
                    if self._current(session):
                        self.completed.emit(session.epoch, message)
        finally:
            await translator.close()

    def run(self):
        if not self.config.use_streaming_translation:
            return
        try:
            asyncio.run(self._run_async())
        except Exception:
            session = self.bus.current()
            if session and self._current(session):
                session.error = 'Streaming worker stopped. Restart the app and check dependencies.'
                session.ready.set()
                self.state.finish_recording(session.epoch)
                self.error.emit(session.epoch, session.error)
                self.completed.emit(session.epoch, session.error)
