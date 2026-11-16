"""Thread-safe control epochs and bounded messages shared by workers."""
from __future__ import annotations
from dataclasses import dataclass, replace
from enum import Enum
from queue import Empty, Full, Queue
from threading import Lock
import time
from typing import TypeVar
import numpy as np

SAMPLE_RATE = 16000


class Mode(str, Enum):
    TRANSCRIPT = "transcript"
    TRANSLATION = "translation"


class Source(str, Enum):
    SYSTEM = "system"
    MIC = "mic"


@dataclass(frozen=True)
class Snapshot:
    epoch: int
    mode: Mode
    paused: bool
    ready: bool
    stopped: bool
    recording: bool = False

    def accepts(self, source: Source) -> bool:
        return (self.ready and not self.paused and not self.stopped
                and (self.mode == Mode.TRANSCRIPT or source == Source.MIC))


class PipelineState:
    def __init__(self) -> None:
        self._lock = Lock()
        self._value = Snapshot(0, Mode.TRANSCRIPT, False, False, False)

    def snapshot(self) -> Snapshot:
        with self._lock:
            return self._value

    def update(self, *, mode: Mode | None = None, paused: bool | None = None,
               ready: bool | None = None, stopped: bool | None = None) -> Snapshot:
        with self._lock:
            old = self._value
            values = (old.mode if mode is None else mode,
                      old.paused if paused is None else paused,
                      old.ready if ready is None else ready,
                      old.stopped if stopped is None else stopped)
            if values != (old.mode, old.paused, old.ready, old.stopped):
                self._value = Snapshot(old.epoch + 1, *values)
            return self._value

    def begin_recording(self) -> Snapshot:
        with self._lock:
            old = self._value
            if old.mode != Mode.TRANSLATION or not old.accepts(Source.MIC) or old.recording:
                raise ValueError("Recording is not available in the current state.")
            self._value = replace(old, epoch=old.epoch + 1, recording=True)
            return self._value

    def finish_recording(self, epoch: int) -> None:
        with self._lock:
            if self._value.epoch == epoch:
                # Stop capture without invalidating the utterance awaiting ASR.
                self._value = replace(self._value, recording=False)

    def invalidate(self) -> Snapshot:
        with self._lock:
            self._value = replace(self._value, epoch=self._value.epoch + 1, recording=False)
            return self._value

    def accepts(self, epoch: int, source: Source) -> bool:
        current = self.snapshot()
        return current.epoch == epoch and current.accepts(source)


@dataclass(frozen=True)
class AudioChunk:
    audio: np.ndarray
    source: Source
    mode: Mode
    epoch: int
    speech_end: float
    queued_at: float
    overlaps_previous: bool = False


@dataclass(frozen=True)
class SpeechResult:
    text: str
    source: Source
    mode: Mode
    epoch: int
    inference_s: float
    end_to_text_s: float
    audio_s: float


@dataclass(frozen=True)
class RecognizedText:
    text: str
    epoch: int


@dataclass(frozen=True)
class TextRequest:
    text: str
    epoch: int
    queued_at: float
    source: Source = Source.MIC
    mode: Mode = Mode.TRANSLATION


T = TypeVar("T")


def put_latest(queue: Queue[T], item: T) -> bool:
    """Avoid waiting for queue space; return whether an older item was dropped.

    queue.Queue and control state use short locks. This is not a lock-free or
    hard-real-time callback design.
    """
    dropped = False
    while True:
        try:
            queue.put_nowait(item)
            return dropped
        except Full:
            try:
                queue.get_nowait()
                dropped = True
            except Empty:
                continue


def drain(queue: Queue[T]) -> None:
    while True:
        try:
            queue.get_nowait()
        except Empty:
            return


def expired(chunk: AudioChunk, max_age: float) -> bool:
    return time.monotonic() - chunk.queued_at > max_age
