"""API keys encrypted by Windows DPAPI for the signed-in Windows user."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
import tempfile

from preferences import settings_path


class CredentialError(RuntimeError):
    pass


class Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def validate_key(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("API key must be text.")
    value = value.strip()
    if len(value) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise ValueError("API keys must contain printable ASCII without spaces.")
    return value


def credential_path(provider: str) -> Path:
    if provider not in ("gemini", "groq"):
        raise ValueError("Unknown credential provider.")
    return settings_path().parent / (provider + ".dpapi")


def _crypt(data: bytes, decrypt: bool) -> bytes:
    if sys.platform != "win32":
        raise CredentialError("Saving keys requires Windows DPAPI. Environment keys are supported for tests.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    signature = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                 ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    operation.argtypes = signature
    operation.restype = wintypes.BOOL
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = Blob(len(data), buffer)
    target = Blob()
    # CRYPTPROTECT_UI_FORBIDDEN: no unprotected native credential prompts.
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise CredentialError("Windows could not decrypt/save this key. Re-enter it on this Windows account.")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        if target.data:
            ctypes.memset(target.data, 0, target.size)
            kernel32.LocalFree(target.data)
        ctypes.memset(buffer, 0, len(data))


def read_key(provider: str) -> str:
    try:
        path = credential_path(provider)
        if not path.exists():
            return ""
        if path.stat().st_size > 16384:
            raise ValueError()
        return validate_key(_crypt(path.read_bytes(), True).decode("utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise CredentialError("Saved key cannot be read. Remove it in Settings and enter it again.") from exc


def write_key(provider: str, value: str) -> None:
    path = credential_path(provider)
    value = validate_key(value)
    if not value:
        path.unlink(missing_ok=True)
        return
    encrypted = _crypt(value.encode("utf-8"), False)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as output:
            temporary = output.name
            output.write(encrypted)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
