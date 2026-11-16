"""Single-owner QThread inference with local CUDA/CPU and explicit Groq backends."""
from __future__ import annotations
from dataclasses import replace
import gc
import io
import logging
from queue import Empty, Queue
import re
import sys
from threading import Event
import time
from typing import Callable
import wave
import numpy as np
from runtime_bootstrap import preload_onnx
preload_onnx()  # Must precede the first Qt import on Windows.
from PyQt6.QtCore import QThread, pyqtSignal
from config import AppConfig
from cuda_runtime import CudaRuntimeError, WindowsCudaRuntime
from pipeline import AudioChunk, Mode, PipelineState, RecognizedText, SAMPLE_RATE, SpeechResult, TextRequest, expired
from text_translation import TextTranslator, TranslationCancelled, TranslationError

log = logging.getLogger(__name__)


class FatalBackendError(RuntimeError):
    """An account or setup failure that retrying another audio chunk cannot fix."""


class Cancelled(RuntimeError):
    """An obsolete request was cancelled after a control change."""


def _normal(word: str) -> str:
    return re.sub(r"[^\w]", "", word, flags=re.UNICODE).casefold()


def remove_overlap(previous: str, text: str) -> str:
    old, new = previous.split(), text.split()
    for count in range(min(8, len(old), len(new)), 1, -1):
        if [_normal(w) for w in old[-count:]] == [_normal(w) for w in new[:count]]:
            return " ".join(new[count:])
    return text


def wav_bytes(audio: np.ndarray) -> bytes:
    buf = io.BytesIO()
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(buf, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(pcm.tobytes())
    return buf.getvalue()


class LocalWhisperBackend:
    def __init__(self, config: AppConfig, notify: Callable[[str], None]) -> None:
        self.config, self.notify = config, notify
        self.model = None
        self.device = "cpu"
        self.loaded_name = config.model
        self.fallback_reason: str | None = None
        self._runtime = WindowsCudaRuntime(config.cuda_bin)

    def _cuda_preflight(self) -> None:
        if sys.platform == "win32":
            report = self._runtime.prepare()
            log.info("CUDA runtime loaded from %d discovered DLL files.", len(report["loaded_libraries"]))

    def _load_on(self, device: str, model_name: str | None = None) -> None:
        from faster_whisper import WhisperModel
        name = model_name or self.config.model
        self.model = WhisperModel(
            name, device=device,
            compute_type="int8_float16" if device == "cuda" else "int8",
            cpu_threads=self.config.cpu_threads, num_workers=1,
            download_root=self.config.model_cache, local_files_only=self.config.offline,
        )
        self.device = device
        self.loaded_name = name
        silence = np.zeros(SAMPLE_RATE, dtype=np.float32)
        # The unfiltered decode exercises the native model/CUDA DLLs. The second
        # call initializes ONNX Silero before capture is marked ready.
        for use_vad in (False, True):
            segments, _ = self.model.transcribe(
                silence, language="en", task="transcribe", beam_size=1,
                temperature=0.0, vad_filter=use_vad, condition_on_previous_text=False,
            )
            list(segments)

    def load(self) -> str:
        if self.config.device != "cpu":
            stage = "CUDA runtime discovery"
            try:
                self._cuda_preflight()
                stage = "CUDA device detection"
                import ctranslate2
                if ctranslate2.get_cuda_device_count() < 1:
                    raise CudaRuntimeError("No CUDA device was detected. Check the NVIDIA driver and GPU availability.")
                stage = "GPU model load/warm-up"
                self._load_on("cuda")
                return f"Local {self.config.model} · CUDA int8/float16"
            except Exception as exc:
                detail = (str(exc) if isinstance(exc, CudaRuntimeError)
                          else f"{stage} failed ({type(exc).__name__}); run main.py --diagnose.")
                self.fallback_reason = detail
                log.warning("CPU fallback: %s", detail)
                self.notify("CPU fallback: " + detail)
                self.model = None
                gc.collect()
        self._load_on("cpu")
        return f"Local {self.config.model} · CPU int8"

    def _decode(self, chunk: AudioChunk) -> str:
        dictating = chunk.mode == Mode.TRANSLATION
        options = dict(
            task="transcribe",
            language=(self.config.translation_language if dictating else self.config.transcript_language),
            beam_size=5 if dictating else 1, best_of=5 if dictating else 1, temperature=0.0,
            condition_on_previous_text=dictating, without_timestamps=True,
            # Dictation keeps the whole utterance; only non-speech is removed.
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=160, speech_pad_ms=120,
                                min_speech_duration_ms=100),
            no_speech_threshold=0.6, log_prob_threshold=-1.0,
            compression_ratio_threshold=2.4,
        )
        if self.config.initial_prompt:
            options["initial_prompt"] = self.config.initial_prompt
        segments, _ = self.model.transcribe(chunk.audio, **options)
        # Deliberately no phrase blocklist: real speech may say "Thank you."
        return " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()

    def recognize(self, chunk: AudioChunk) -> str:
        name = self.config.dictation_model if chunk.mode == Mode.TRANSLATION else self.config.model
        try:
            if self.model is None or self.loaded_name != name:
                self.notify(f"Loading {name} for {'dictation' if chunk.mode == Mode.TRANSLATION else 'live captions'}…")
                self.release_model()
                self._load_on(self.device, name)
            return self._decode(chunk)
        except (RuntimeError, OSError) as exc:
            if self.device != "cuda":
                raise
            self.fallback_reason = f"GPU inference failed ({type(exc).__name__}); run main.py --diagnose."
            log.warning("CPU fallback: %s", self.fallback_reason)
            self.notify("CPU fallback: " + self.fallback_reason)
            self.model = None
            gc.collect()
            self._load_on("cpu", name)
            return self._decode(chunk)

    def release_model(self) -> None:
        self.model = None
        gc.collect()

    def close(self) -> None:
        self.release_model()
        self._runtime.close()


class GroqWhisperBackend:
    """Cloud upload is explicit opt-in and never a local-inference fallback."""
    def __init__(self, config: AppConfig, stop_event: Event, state: PipelineState, client=None):
        self.config, self.stop_event, self.state = config, stop_event, state
        self.client = client

    def load(self) -> str:
        if self.client is None:
            import httpx
            self.client = httpx.Client(
                timeout=httpx.Timeout(connect=5.0, read=45.0, write=15.0, pool=2.0),
                follow_redirects=False, trust_env=False,
            )
        return f"Groq cloud · {self.config.model}"

    def recognize(self, chunk: AudioChunk) -> str:
        import httpx
        if not self.config.cloud_enabled:
            raise FatalBackendError("Cloud services are disabled. Enable them in Settings or choose Local Whisper.")
        if not self.config.groq_api_key:
            raise FatalBackendError("Add a Groq API key in Settings before using cloud dictation.")
        dictating = chunk.mode == Mode.TRANSLATION
        endpoint = "transcriptions"
        model = self.config.dictation_model if dictating else self.config.model
        fields = {"model": model, "response_format": "json", "temperature": "0"}
        language = self.config.translation_language if dictating else self.config.transcript_language
        if language:
            fields["language"] = language
        if self.config.initial_prompt:
            fields["prompt"] = self.config.initial_prompt
        wav = wav_bytes(chunk.audio)
        from network_io import RequestCancelled, provider_error, request_json
        cancelled = lambda: self.stop_event.is_set() or not self.state.accepts(chunk.epoch, chunk.source)
        for attempt in range(2):
            if self.stop_event.is_set() or not self.state.accepts(chunk.epoch, chunk.source):
                raise Cancelled()
            try:
                code, data, headers = request_json(self.client, "POST",
                    f"https://api.groq.com/openai/v1/audio/{endpoint}",
                    headers={"Authorization": f"Bearer {self.config.groq_api_key}"},
                    data=fields, files={"file": ("speech.wav", wav, "audio/wav")},
                    cancelled=cancelled, max_seconds=60,
                )
            except RequestCancelled:
                raise Cancelled() from None
            except ValueError:
                raise RuntimeError("Groq returned an invalid or oversized response.") from None
            except httpx.TransportError:
                if attempt == 0 and not self.stop_event.wait(0.3):
                    continue
                raise RuntimeError("Cloud network request failed; check connection.") from None
            if code in (400, 401, 403, 404, 413, 422):
                raise FatalBackendError(provider_error("Groq", code, data, (self.config.groq_api_key,)))
            if code == 429 or 500 <= code <= 599:
                if attempt == 0:
                    try:
                        delay = max(0.25, min(3.0, float(headers.get("retry-after", "0.5"))))
                    except ValueError:
                        delay = 0.5
                    if self.stop_event.wait(delay):
                        raise Cancelled()
                    continue
                raise RuntimeError(provider_error("Groq", code, data, (self.config.groq_api_key,)))
            if code != 200:
                raise RuntimeError(provider_error("Groq", code, data, (self.config.groq_api_key,)))
            try:
                text = data["text"]
                if not isinstance(text, str) or len(text) > 8000:
                    raise ValueError()
            except (ValueError, KeyError, TypeError):
                raise RuntimeError("Groq returned an invalid text response.") from None
            return text.strip()
        raise RuntimeError("Cloud retry exhausted.")

    def close(self) -> None:
        if self.client is not None:
            self.client.close()


class SpeechWorker(QThread):
    result = pyqtSignal(object)
    ready = pyqtSignal(str)
    busy = pyqtSignal(bool)
    status = pyqtSignal(str)
    fault = pyqtSignal(str)
    recognized = pyqtSignal(object)
    dictation_done = pyqtSignal(int, str)

    def __init__(self, config: AppConfig, state: PipelineState, chunks: Queue[AudioChunk],
                 backend: LocalWhisperBackend | GroqWhisperBackend | None = None, translator=None):
        super().__init__()
        self.setObjectName("speech-inference")
        self.config, self.state, self.chunks = config, state, chunks
        self._stop_event = Event()
        self._backend = backend
        self._injected = backend is not None
        self._dictation_backend = None
        self._translator = translator or TextTranslator(config, self._stop_event, state)

    def _make_backend(self, kind, dictation=False):
        if kind == "gemini":
            from gemini_speech import GeminiSpeechBackend
            return GeminiSpeechBackend(self.config, self._stop_event, self.state, live=not dictation)
        config = replace(self.config, backend=kind, model=self.config.dictation_model) if dictation else self.config
        return (LocalWhisperBackend(config, self.status.emit) if kind == "local"
                else GroqWhisperBackend(config, self._stop_event, self.state))

    def _for_mode(self, mode):
        if (mode == Mode.TRANSCRIPT or self._injected or
                self.config.dictation_backend == self.config.backend != "gemini"):
            return self._backend
        if self._dictation_backend is None:
            candidate = self._make_backend(self.config.dictation_backend, dictation=True)
            try:
                candidate.load()
            except Exception:
                candidate.close()
                raise
            self._dictation_backend = candidate
        return self._dictation_backend

    def request_stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        history = {}
        failures = 0
        last_stale_notice = 0.0
        try:
            self.status.emit("Loading speech engine…")
            if self._backend is None:
                self._backend = self._make_backend(self.config.backend)
            self.ready.emit(self._backend.load())
            if self._stop_event.is_set():
                return
            self.state.update(ready=True)
            while not self._stop_event.is_set():
                try:
                    job = self.chunks.get(timeout=0.1)
                except Empty:
                    continue
                if not self.state.accepts(job.epoch, job.source):
                    continue
                manual = job.mode == Mode.TRANSLATION
                if not manual and expired(job, self.config.max_queue_age_s):
                    if time.monotonic() - last_stale_notice > 3:
                        self.status.emit("Old queued audio skipped to keep up with the meeting.")
                        last_stale_notice = time.monotonic()
                    continue
                self.busy.emit(True)
                started = time.monotonic()
                notice = ""
                try:
                    if isinstance(job, TextRequest):
                        source_text = job.text
                    else:
                        backend = self._for_mode(job.mode)
                        self.status.emit("Recognizing your complete utterance…" if manual else "Transcribing…")
                        source_text = backend.recognize(job)
                        if isinstance(backend, LocalWhisperBackend):
                            compute = "CUDA int8/float16" if backend.device == "cuda" else "CPU int8"
                            self.ready.emit(f"Local {backend.loaded_name} · {compute}")
                        elif manual and isinstance(backend, GroqWhisperBackend):
                            self.ready.emit(f"Cloud dictation · {backend.config.dictation_model}")
                    if not self.state.accepts(job.epoch, job.source) or self._stop_event.is_set():
                        continue
                    if manual:
                        if not source_text.strip():
                            notice = "No speech recognized. Record again or enter text to translate."
                            continue
                        self.recognized.emit(RecognizedText(source_text, job.epoch))
                        if self.config.text_translator == "ollama":
                            for candidate in (self._backend, self._dictation_backend):
                                if isinstance(candidate, LocalWhisperBackend):
                                    candidate.release_model()
                        self.status.emit("Translating the recognized words into English…")
                        text = self._translator.translate(source_text, job.epoch)
                    else:
                        text = source_text
                    if not self.state.accepts(job.epoch, job.source) or self._stop_event.is_set():
                        continue
                    failures = 0
                    if not manual:
                        previous = history.get(job.source)
                        if (job.overlaps_previous and previous and previous[0] == job.epoch
                                and 0 <= job.speech_end - previous[2] <= 3.2):
                            text = remove_overlap(previous[1], text)
                        history[job.source] = (job.epoch, text, job.speech_end)
                    if text:
                        now = time.monotonic()
                        self.result.emit(SpeechResult(text, job.source, job.mode, job.epoch,
                            now - started, max(0, now - (job.queued_at if isinstance(job, TextRequest) else job.speech_end)),
                            0 if isinstance(job, TextRequest) else len(job.audio) / SAMPLE_RATE))
                    if manual:
                        notice = "English ready. Correct the recognized words and Translate text again if needed."
                except (Cancelled, TranslationCancelled):
                    continue
                except Exception as exc:
                    if not self.state.accepts(job.epoch, job.source) or self._stop_event.is_set():
                        continue
                    if manual:
                        notice = (str(exc) if isinstance(exc, (TranslationError, FatalBackendError))
                                  else f"Dictation failed ({type(exc).__name__}). Check Settings and try again.")
                        log.warning("Manual dictation/translation failed: %s", type(exc).__name__)
                    else:
                        if isinstance(exc, FatalBackendError):
                            raise
                        failures += 1
                        self.status.emit(f"Speech request failed ({type(exc).__name__}); chunk skipped.")
                        if failures >= 3:
                            raise FatalBackendError("Three consecutive live speech failures. Check diagnostics and restart.") from None
                finally:
                    self.busy.emit(False)
                    if manual:
                        self.dictation_done.emit(job.epoch, notice)
        except Exception as exc:
            self.state.update(ready=False)
            if not self._stop_event.is_set():
                message = (str(exc) if isinstance(exc, FatalBackendError)
                           else f"Speech engine startup failed ({type(exc).__name__}). Run main.py --diagnose; see SETUP.md.")
                self.fault.emit(message)
                log.error("Speech worker stopped: %s", type(exc).__name__)
        finally:
            self.busy.emit(False)
            for component in (self._backend, self._dictation_backend, self._translator):
                if component is not None:
                    try:
                        component.close()
                    except Exception:
                        log.warning("Speech component cleanup failed")
