"""Typed Win32 capture exclusion and system-wide local visibility hotkey.

Affinity is a best-effort Windows capture policy, not a security boundary or a
guarantee that every recorder respects it.
"""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import sys

WDA_EXCLUDEFROMCAPTURE = 0x00000011
WM_HOTKEY = 0x0312
HOTKEY_ID = 0x4844
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000


class ProtectionError(RuntimeError):
    """The HUD must stay hidden when capture exclusion cannot be verified."""


class CaptureGuard:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise ProtectionError("This application requires Windows 10 2004+ or Windows 11.")
        if sys.getwindowsversion().build < 19041:
            raise ProtectionError("Capture exclusion requires Windows build 19041 or later.")
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        self._user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        self._user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
        self._user32.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self._user32.GetWindowDisplayAffinity.restype = wintypes.BOOL
        self._user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self._user32.RegisterHotKey.restype = wintypes.BOOL
        self._user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self._user32.UnregisterHotKey.restype = wintypes.BOOL
        self._dwmapi.DwmIsCompositionEnabled.argtypes = [ctypes.POINTER(wintypes.BOOL)]
        self._dwmapi.DwmIsCompositionEnabled.restype = ctypes.c_long
        self._hwnd: int | None = None

    @staticmethod
    def _failure(operation: str) -> ProtectionError:
        return ProtectionError(f"{operation} failed (Win32 {ctypes.get_last_error()}). HUD hidden.")

    def _check_dwm(self) -> None:
        enabled = wintypes.BOOL()
        hr = self._dwmapi.DwmIsCompositionEnabled(ctypes.byref(enabled))
        if hr < 0 or not enabled.value:
            raise ProtectionError("Desktop Window Manager composition is unavailable. HUD hidden.")

    def verify(self, hwnd: int) -> None:
        self._check_dwm()
        affinity = wintypes.DWORD()
        ctypes.set_last_error(0)
        if not self._user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(affinity)):
            raise self._failure("GetWindowDisplayAffinity")
        if affinity.value != WDA_EXCLUDEFROMCAPTURE:
            raise ProtectionError("Capture exclusion readback is not 0x11. HUD hidden.")

    def protect(self, hwnd: int) -> None:
        self._check_dwm()
        ctypes.set_last_error(0)
        if not self._user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
            raise self._failure("SetWindowDisplayAffinity")
        self.verify(hwnd)
        if self._hwnd != hwnd:
            self.close()
            if not self._user32.RegisterHotKey(hwnd, HOTKEY_ID,
                                              MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, ord("H")):
                raise self._failure("Ctrl+Shift+H registration (shortcut may be in use)")
            self._hwnd = hwnd

    def close(self) -> None:
        if self._hwnd is not None:
            self._user32.UnregisterHotKey(self._hwnd, HOTKEY_ID)
            self._hwnd = None
