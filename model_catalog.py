"""Read-only, bounded provider discovery. No generation or audio upload occurs here."""
from __future__ import annotations

from dataclasses import dataclass, field
from queue import Empty, Queue
import re
from threading import Event
import time

import httpx
from PyQt6.QtCore import QThread, pyqtSignal

from credentials import validate_key
from gemini_protocol import valid_speech_model

LOCAL_MODELS = ("tiny", "base", "small", "medium", "large-v2", "large-v3", "turbo")
MODEL_ID = re.compile(r"[A-Za-z0-9._:/-]{1,240}")


class CatalogError(RuntimeError):
    pass


@dataclass(frozen=True)
class CatalogRequest:
    token: int
    provider: str
    stage: str
    key: str = field(default="", repr=False)


@dataclass(frozen=True)
class CatalogResult:
    token: int
    provider: str
    stage: str
    models: tuple[str, ...]
    error: str = ""


def local_ollama_model(item: dict) -> bool:
    name = item.get("name", "")
    return (isinstance(name, str) and bool(MODEL_ID.fullmatch(name)) and ":" in name
            and not item.get("remote_host") and not item.get("remote_model")
            and not name.endswith(":cloud") and "-cloud" not in name)


def fetch_models(request: CatalogRequest, client=None, stop_event=None) -> tuple[str, ...]:
    if request.provider == "local":
        return LOCAL_MODELS
    if request.provider not in ("gemini", "groq", "ollama"):
        raise CatalogError("Unsupported model provider.")
    key = validate_key(request.key)
    if request.provider != "ollama" and not key:
        raise CatalogError("Enter this provider's API key first.")
    owned = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(8, connect=4, pool=2),
                                    follow_redirects=False, trust_env=False)
    urls = {"gemini": "https://generativelanguage.googleapis.com/v1beta/models",
            "groq": "https://api.groq.com/openai/v1/models", "ollama": "http://127.0.0.1:11434/api/tags"}
    headers = ({"x-goog-api-key": key} if request.provider == "gemini" else
               {"Authorization": "Bearer " + key} if request.provider == "groq" else {})
    models, seen_tokens = set(), set()
    params = {"pageSize": 1000} if request.provider == "gemini" else {}
    deadline = time.monotonic() + 20
    try:
        for _ in range(10):
            if (stop_event and stop_event.is_set()) or time.monotonic() > deadline:
                raise CatalogError("Model discovery stopped or timed out.")
            try:
                with client.stream("GET", urls[request.provider], headers=headers, params=params) as response:
                    if response.status_code != 200:
                        raise CatalogError(f"Model list returned HTTP {response.status_code}. Check key, access and quota.")
                    parts, size = [], 0
                    for part in response.iter_bytes():
                        size += len(part)
                        if size > 2_000_000 or time.monotonic() > deadline or (stop_event and stop_event.is_set()):
                            raise CatalogError("Model list exceeded its size/time limit or was cancelled.")
                        parts.append(part)
                    import json
                    data = json.loads(b"".join(parts))
                rows = data.get("data" if request.provider == "groq" else "models", [])
                if not isinstance(rows, list):
                    raise ValueError()
                for row in rows:
                    if not isinstance(row, dict):
                        raise ValueError()
                    if request.provider == "gemini":
                        name = row.get("name", "").removeprefix("models/")
                        methods = row.get("supportedGenerationMethods", [])
                        usable = ("generateContent" in methods
                                  and name.startswith("gemini-")
                                  and not any(x in name for x in ("image", "tts", "robotics", "computer-use", "live", "transcribe")))
                        if request.stage == "live":
                            usable = valid_speech_model(name, live=True) and any("bidigeneratecontent" == m.lower() for m in methods)
                        elif request.stage == "dictation":
                            usable = valid_speech_model(name) and ("transcribe" in name or "generateContent" in methods)
                    elif request.provider == "groq":
                        name = row.get("id", "")
                        usable = (name.startswith("whisper-") if request.stage != "text" else
                                  not any(x in name for x in ("whisper", "tts", "guard", "orpheus", "embed", "compound")))
                        usable = usable and row.get("active", True)
                    else:
                        name, usable = row.get("name", ""), local_ollama_model(row)
                    if usable and isinstance(name, str) and MODEL_ID.fullmatch(name):
                        models.add(name)
                token = data.get("nextPageToken") if request.provider == "gemini" else None
                if not token:
                    return tuple(sorted(models))
                if not isinstance(token, str) or len(token) > 4096 or token in seen_tokens:
                    raise ValueError()
                seen_tokens.add(token)
                params["pageToken"] = token
            except httpx.TransportError:
                raise CatalogError("Cannot reach provider. Check connection, or start Ollama for local models.") from None
            except (ValueError, TypeError, AttributeError):
                raise CatalogError("Provider returned an invalid model list.") from None
        raise CatalogError("Too many pages in the provider model list.")
    finally:
        if owned:
            client.close()


class CatalogWorker(QThread):
    discovered = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.setObjectName("model-discovery")
        self.jobs = Queue(maxsize=1)
        self.stop_event = Event()

    def submit(self, request):
        from pipeline import put_latest
        put_latest(self.jobs, request)

    def request_stop(self):
        self.stop_event.set()

    def run(self):
        while not self.stop_event.is_set():
            try:
                request = self.jobs.get(timeout=.1)
            except Empty:
                continue
            try:
                models = fetch_models(request, stop_event=self.stop_event)
                result = CatalogResult(request.token, request.provider, request.stage, models)
            except Exception as exc:
                error = str(exc) if isinstance(exc, (CatalogError, ValueError)) else "Model discovery failed. Check provider settings."
                result = CatalogResult(request.token, request.provider, request.stage, (), error)
            if not self.stop_event.is_set():
                self.discovered.emit(result)
