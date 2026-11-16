"""Validated configuration. Secrets and glossary text are excluded from repr."""
from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
from pathlib import Path
from preferences import read_preferences

APP_VERSION = "1.4.0"


def language(value: str) -> str | None:
    value = value.strip().lower()
    if value in ("", "auto"):
        return None
    if not re.fullmatch(r"[a-z]{2,3}", value):
        raise ValueError("Language must be auto or a Whisper language code such as hi/en/ta.")
    return value


@dataclass(frozen=True)
class AppConfig:
    backend: str = "local"
    model: str = "small"
    device: str = "auto"
    translation_language: str | None = "hi"
    transcript_language: str | None = None
    cpu_threads: int = 6
    min_chunk_s: float = 0.8
    max_chunk_s: float = 3.0
    pause_s: float = 0.24
    energy_floor: float = 0.003
    queue_size: int = 4
    max_queue_age_s: float = 5.0
    offline: bool = False
    model_cache: str | None = None
    cuda_bin: str | None = None
    initial_prompt: str = field(default="", repr=False)
    groq_api_key: str = field(default="", repr=False)
    gemini_api_key: str = field(default="", repr=False)
    credential_notice: str = field(default="", repr=False)
    cloud_enabled: bool = False
    dictation_backend: str = "local"
    dictation_model: str = "large-v3"
    text_translator: str = "ollama"
    text_model: str = "llama-3.3-70b-versatile"
    ollama_model: str = "qwen3:4b-instruct"
    gemini_model: str = ""
    max_record_s: float = 60.0
    microphone: str = ""
    noise_suppression: bool = False

    def __post_init__(self) -> None:
        if self.backend not in ("local", "groq", "gemini"):
            raise ValueError("HUD_BACKEND must be local, groq or gemini.")
        if self.dictation_backend not in ("local", "groq", "gemini") or self.text_translator not in ("groq", "ollama", "gemini"):
            raise ValueError("Choose local/Groq/Gemini speech and Ollama/Groq/Gemini text translation.")
        if len(self.microphone) > 512 or any(ord(c) < 32 for c in self.microphone):
            raise ValueError("Invalid microphone identity. Select a microphone in Settings.")
        from gemini_protocol import valid_speech_model
        if self.backend == "gemini" and not valid_speech_model(self.model, live=True):
            raise ValueError("Select a Gemini transcribe-live model for continuous live captions.")
        if self.dictation_backend == "gemini" and not valid_speech_model(self.dictation_model):
            raise ValueError("Select a Gemini Transcribe or audio-capable Flash/Pro model for recognition.")
        if not 5 <= self.max_record_s <= 120:
            raise ValueError("HUD_MAX_RECORD_SECONDS must be between 5 and 120.")
        if not self.model or not self.dictation_model or not self.text_model or not self.ollama_model:
            raise ValueError("Model names cannot be empty.")
        if self.dictation_backend == "local" and ("distil" in self.dictation_model.lower() or self.dictation_model.endswith(".en")):
            raise ValueError("Choose a multilingual dictation model such as medium, large-v2 or large-v3.")
        if self.device not in ("auto", "cuda", "cpu"):
            raise ValueError("HUD_DEVICE must be auto, cuda, or cpu (cuda still falls back).")
        for name in (self.model, self.dictation_model, self.text_model, self.ollama_model, self.gemini_model):
            if len(name) > 240 or any(ord(c) < 32 for c in name):
                raise ValueError("Invalid model identifier.")
        for provider, name in ((self.backend, self.model), (self.dictation_backend, self.dictation_model)):
            if provider == "groq" and not name.startswith("whisper-"):
                raise ValueError("Select a Groq Whisper speech model for recognition.")
        if self.gemini_model and not re.fullmatch(r"[A-Za-z0-9._-]+", self.gemini_model):
            raise ValueError("Use the Gemini model ID without a URL or models/ prefix.")
        if ":" not in self.ollama_model or self.ollama_model.endswith(":cloud") or "-cloud" in self.ollama_model:
            raise ValueError("Choose an explicit local Ollama model:tag; cloud models are not allowed in local mode.")
        if not 1 <= self.cpu_threads <= 64:
            raise ValueError("HUD_CPU_THREADS must be between 1 and 64.")
        if not 0.8 <= self.min_chunk_s <= self.max_chunk_s <= 3.0:
            raise ValueError("Chunk limits must satisfy 0.8 <= min <= max <= 3.0 seconds.")
        if not 0.12 <= self.pause_s <= 0.8:
            raise ValueError("HUD_PAUSE_MS must be between 120 and 800.")
        if not 0.0001 <= self.energy_floor <= 0.1:
            raise ValueError("HUD_ENERGY_FLOOR must be between 0.0001 and 0.1.")
        if not 1 <= self.queue_size <= 16 or not 1 <= self.max_queue_age_s <= 20:
            raise ValueError("Invalid queue bounds.")
        from credentials import validate_key
        validate_key(self.groq_api_key)
        validate_key(self.gemini_api_key)
        prompt = " ".join(self.initial_prompt.split())
        if "\x00" in prompt or len(prompt) > 200:
            raise ValueError("HUD_INITIAL_PROMPT must be at most 200 characters and contain no NUL.")
        object.__setattr__(self, "initial_prompt", prompt)

    @classmethod
    def from_env(cls) -> "AppConfig":
        notices = []
        try:
            saved = read_preferences()
        except ValueError:
            saved = {}
            notices.append("Saved settings could not be read; local defaults are active. Open Settings to save them again.")
        from credentials import read_key, CredentialError
        keys = {}
        for provider, variable in (("groq", "GROQ_API_KEY"), ("gemini", "GEMINI_API_KEY")):
            if variable in os.environ:
                keys[provider] = os.environ[variable].strip()
            else:
                try:
                    keys[provider] = read_key(provider)
                except CredentialError:
                    keys[provider] = ""
                    notices.append(f"Re-enter your {provider} key in Settings.")
        return cls(
            backend=os.getenv("HUD_BACKEND", saved.get("backend", "local")).lower(),
            model=os.getenv("HUD_MODEL", saved.get("model", "small")),
            device=os.getenv("HUD_DEVICE", saved.get("device", "auto")).lower(),
            translation_language=language(os.getenv("HUD_TRANSLATION_LANGUAGE", saved.get("translation_language", "hi"))),
            transcript_language=language(os.getenv("HUD_TRANSCRIPT_LANGUAGE", saved.get("transcript_language", "auto"))),
            cpu_threads=int(os.getenv("HUD_CPU_THREADS", "6")),
            min_chunk_s=float(os.getenv("HUD_MIN_CHUNK_MS", "800")) / 1000,
            max_chunk_s=float(os.getenv("HUD_MAX_CHUNK_MS", "3000")) / 1000,
            pause_s=float(os.getenv("HUD_PAUSE_MS", "240")) / 1000,
            energy_floor=float(os.getenv("HUD_ENERGY_FLOOR", "0.003")),
            offline=os.getenv("HUD_OFFLINE", "0") == "1",
            model_cache=os.getenv("HUD_MODEL_CACHE") or str(Path(__file__).resolve().parent / "models"),
            cuda_bin=os.getenv("HUD_CUDA_BIN") or None,
            initial_prompt=os.getenv("HUD_INITIAL_PROMPT", saved.get("initial_prompt", "")),
            groq_api_key=keys["groq"], gemini_api_key=keys["gemini"], credential_notice=" ".join(notices),
            cloud_enabled=os.getenv("HUD_CLOUD_ENABLED", saved.get("cloud_enabled", "0")) == "1",
            dictation_backend=os.getenv("HUD_DICTATION_BACKEND", saved.get("dictation_backend", "local")),
            dictation_model=os.getenv("HUD_DICTATION_MODEL", saved.get("dictation_model", "large-v3")),
            text_translator=os.getenv("HUD_TEXT_TRANSLATOR", saved.get("text_translator", "ollama")),
            text_model=os.getenv("HUD_TEXT_MODEL", saved.get("text_model", "llama-3.3-70b-versatile")),
            gemini_model=os.getenv("HUD_GEMINI_MODEL", saved.get("gemini_model", "")),
            ollama_model=os.getenv("HUD_OLLAMA_MODEL", saved.get("ollama_model", "qwen3:4b-instruct")),
            max_record_s=float(os.getenv("HUD_MAX_RECORD_SECONDS", "60")),
            microphone=os.getenv("HUD_MICROPHONE", saved.get("microphone", "")),
            noise_suppression=os.getenv("HUD_NOISE_SUPPRESSION", saved.get("noise_suppression", "0")) == "1",
        )
