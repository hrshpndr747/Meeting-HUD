"""Pure Gemini protocol helpers shared by catalogs, ASR and streaming captions."""
from __future__ import annotations
import re


def valid_speech_model(name: str, live=False) -> bool:
    if not isinstance(name, str) or not re.fullmatch(r"gemini-[A-Za-z0-9._-]+", name):
        return False
    if live:
        return "transcribe-live" in name
    if any(word in name for word in ("live", "tts", "image", "robotics", "computer-use", "embedding")):
        return False
    return "transcribe" in name or any(word in name for word in ("flash", "pro"))


def language_hints(language):
    # Hindi dictation commonly includes English names/technical terms. For other
    # languages let automatic recognition handle code switching, without guessing
    # an unsupported regional locale from a two-letter Whisper code.
    if language == "hi":
        return ["hi-IN", "en-IN"]
    if language == "en":
        return ["en-IN", "en-US"]
    return []


def vocabulary(prompt):
    return [item.strip() for item in prompt.split(",") if item.strip()][:100]


def live_setup(config):
    options = {"languageCodes": language_hints(config.transcript_language), "mode": "VERBATIM"}
    if config.initial_prompt:
        options["customVocabulary"] = vocabulary(config.initial_prompt)
    return {"setup": {"model": "models/" + config.model,
                      "generationConfig": {"responseModalities": ["TEXT"]},
                      "inputAudioTranscription": options}}


def input_transcripts(message):
    """Only the user's input, never a generated model reply or audio output."""
    if not isinstance(message, dict):
        raise ValueError("Invalid streaming response.")
    content = message.get("serverContent", {})
    if not isinstance(content, dict):
        raise ValueError("Invalid streaming content.")
    result = []
    for field, final in (("interimInputTranscription", False), ("inputTranscription", True)):
        item = content.get(field)
        if item is None:
            continue
        if not isinstance(item, dict) or not isinstance(item.get("text", ""), str):
            raise ValueError("Invalid streaming transcript.")
        text = item.get("text", "").strip()
        if len(text) > 8000:
            raise ValueError("Streaming transcript exceeded its limit.")
        if text:
            result.append((text, final))
    return result


def interaction_text(data):
    if not isinstance(data, dict) or data.get("status") != "completed":
        raise ValueError("Recognition did not complete. Try a shorter recording.")
    parts = []
    for step in data.get("steps", []):
        if step.get("type") == "model_output":
            for part in step.get("content", []):
                if part.get("type") == "text" and isinstance(part.get("text"), str):
                    parts.append(part["text"])
    text = " ".join(parts).strip()
    if len(text) > 8000:
        raise ValueError("Recognition exceeded its text limit.")
    return text
