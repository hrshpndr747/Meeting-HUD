"""Validate settings before writing any credentials or requesting a restart."""
from __future__ import annotations

from dataclasses import replace
import os
import tempfile

from config import language
from credentials import CredentialError, credential_path, validate_key, write_key
from preferences import ALLOWED, settings_path, write_preferences

ENV_NAMES = {"backend": "HUD_BACKEND", "model": "HUD_MODEL", "device": "HUD_DEVICE",
             "dictation_backend": "HUD_DICTATION_BACKEND", "dictation_model": "HUD_DICTATION_MODEL",
             "text_translator": "HUD_TEXT_TRANSLATOR", "text_model": "HUD_TEXT_MODEL",
             "gemini_model": "HUD_GEMINI_MODEL", "ollama_model": "HUD_OLLAMA_MODEL",
             "cloud_enabled": "HUD_CLOUD_ENABLED", "translation_language": "HUD_TRANSLATION_LANGUAGE",
             "transcript_language": "HUD_TRANSCRIPT_LANGUAGE", "microphone": "HUD_MICROPHONE",
             "noise_suppression": "HUD_NOISE_SUPPRESSION", "initial_prompt": "HUD_INITIAL_PROMPT"}


def save_settings(config, payload):
    values, keys = payload["preferences"], payload["keys"]
    if not isinstance(values, dict) or set(values) - ALLOWED or any(not isinstance(v, str) for v in values.values()):
        raise ValueError("Unsupported settings fields.")
    if not isinstance(keys, dict) or set(keys) - {"gemini", "groq"}:
        raise ValueError("Unsupported credential fields.")
    parsed = dict(values)
    for field in ("cloud_enabled", "noise_suppression"):
        if field in parsed:
            if parsed[field] not in ("0", "1"):
                raise ValueError("Invalid checkbox preference.")
            parsed[field] = parsed[field] == "1"
    for field in ("translation_language", "transcript_language"):
        if field in parsed:
            parsed[field] = language(parsed[field])
    for field, value in parsed.items():
        if ENV_NAMES[field] in os.environ and value != getattr(config, field):
            raise ValueError(f"{ENV_NAMES[field]} overrides this setting. Remove that Windows environment variable first.")
    for provider, value in keys.items():
        value = validate_key(value)
        variable = provider.upper() + "_API_KEY"
        if variable in os.environ and value != getattr(config, provider + "_api_key"):
            raise ValueError(f"{variable} overrides saved credentials. Remove that environment variable first.")
        parsed[provider + "_api_key"] = value
    candidate = replace(config, **parsed)
    providers = {candidate.backend, candidate.dictation_backend, candidate.text_translator} & {"gemini", "groq"}
    if providers:
        if not candidate.cloud_enabled:
            raise ValueError("Choose local providers or explicitly enable cloud services.")
        for provider in providers:
            if not getattr(candidate, provider + "_api_key"):
                raise ValueError(f"The selected {provider} provider needs an API key.")
        if candidate.text_translator == "gemini" and not candidate.gemini_model:
            raise ValueError("Choose a Gemini text model first.")
    paths = [credential_path(provider) for provider in keys] + [settings_path()]
    backups = {path: path.read_bytes() if path.exists() else None for path in paths}
    try:
        # No model download or network generation occurs while saving.
        for provider, value in keys.items():
            write_key(provider, value)
        write_preferences(values)
    except Exception:
        rollback_failed = False
        for path, blob in backups.items():
            temporary = None
            try:
                if blob is None:
                    path.unlink(missing_ok=True)
                else:
                    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as output:
                        temporary = output.name
                        output.write(blob)
                    os.replace(temporary, path)
            except OSError:
                rollback_failed = True
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
        if rollback_failed:
            raise CredentialError("Save was incomplete and rollback failed. Check saved keys and settings before restarting.") from None
        raise
    return candidate
