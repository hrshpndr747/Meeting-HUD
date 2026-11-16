"""Bound response bodies and elapsed streaming reads without logging content."""
from __future__ import annotations
import json
import time


class RequestCancelled(RuntimeError):
    pass


def request_json(client, method, url, *, cancelled=lambda: False,
                 max_bytes=262144, max_seconds=120, **kwargs):
    if cancelled():
        raise RequestCancelled()
    deadline = time.monotonic() + max_seconds
    with client.stream(method, url, **kwargs) as response:
        success = response.status_code == 200
        limit = max_bytes if success else min(max_bytes, 16384)
        chunks, count = [], 0
        for chunk in response.iter_bytes():
            if cancelled():
                raise RequestCancelled()
            count += len(chunk)
            if count > limit or time.monotonic() > deadline:
                if not success:
                    return response.status_code, None, response.headers
                raise ValueError("Response exceeded size or time limit.")
            chunks.append(chunk)
        if cancelled():
            raise RequestCancelled()
        try:
            data = json.loads(b"".join(chunks))
        except (ValueError, UnicodeError):
            if success:
                raise ValueError("Provider returned invalid JSON.") from None
            data = None
        return response.status_code, data, response.headers


def provider_error(provider, status, data=None, secrets=()):
    """Interpret bounded errors without ever returning echoed speech, keys or URLs."""
    message = ""
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        value = data["error"].get("message", "")
        if isinstance(value, str):
            message = value
    # Redact before classification and never display the raw body, even if an
    # upstream proxy echoes a transcript or only a fragment of a credential.
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[redacted]")
    lower = message.casefold()
    if "new user" in lower or "new project" in lower:
        detail = "This model is restricted for new accounts/projects. Load models and select one available to this key."
    elif status in (401, 403):
        detail = "Check the API key, project permissions and region."
    elif status == 404:
        detail = "The selected model is unavailable to this key or endpoint. Load models and choose a compatible model."
    elif status == 429:
        detail = "Quota or rate limit reached. Wait or choose a local model; no automatic paid fallback is used."
    elif status in (400, 413, 422):
        detail = "The selected model rejected this request. Check model compatibility and recording length."
    elif 500 <= status < 600:
        detail = "Provider temporarily unavailable. Retry when the service recovers."
    else:
        detail = "Check the provider connection and model settings."
    return f"{provider} HTTP {status}. {detail}"
