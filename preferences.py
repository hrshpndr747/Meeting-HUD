"""Non-secret local model preferences. API keys are never persisted here."""
from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile

ALLOWED = {"backend", "model", "device", "dictation_backend", "text_translator",
           "dictation_model", "ollama_model", "text_model", "gemini_model",
           "cloud_enabled", "transcript_language", "translation_language",
           "microphone", "noise_suppression", "initial_prompt", "translation_streaming",
           "translation_fast", "dictation_live_model", "caption_font_pt", "caption_lines"}


def settings_path() -> Path:
    # Evaluate fallbacks lazily: Path.home() can fail in a pythonw session even
    # when APPDATA is valid. Never put account credentials in shared temp storage.
    for variable in ("APPDATA", "LOCALAPPDATA", "USERPROFILE"):
        value = os.environ.get(variable, "").strip()
        if value and Path(value).is_absolute():
            return Path(value) / "MeetingHUD" / "settings.json"
    try:
        return Path.home() / ".config" / "MeetingHUD" / "settings.json"
    except (RuntimeError, OSError):
        raise ValueError("Cannot locate your user settings folder. Restore APPDATA in Windows and relaunch.") from None


def read_preferences() -> dict:
    path = settings_path()
    if not path.exists():
        return {}
    try:
        if path.stat().st_size > 65536:
            raise ValueError()
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError()
        return {key: value for key, value in data.items() if key in ALLOWED and isinstance(value, str)}
    except (ValueError, OSError):
        raise ValueError("Cannot read MeetingHUD settings.json. Rename it to reset the settings.") from None


def write_preferences(data: dict) -> None:
    if set(data) - ALLOWED or any(not isinstance(v, str) for v in data.values()):
        raise ValueError("Unsupported preference fields.")
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as output:
            temporary = output.name
            json.dump(data, output, indent=2)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
