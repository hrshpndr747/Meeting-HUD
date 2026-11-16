"""Desktop-scoped single-instance ownership and queued local restore requests.

The launcher retains its handles across child restarts. The GUI listener also
holds the mutex open so a supervisor crash cannot make an existing HUD invisible
to a subsequent shortcut launch. No window is shown by this module: the GUI must
verify capture exclusion before acting on a restore request.
"""
from __future__ import annotations

from contextlib import suppress
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import sys

ERROR_FILE_NOT_FOUND = 2
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_ALREADY_EXISTS = 183
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
WAIT_FAILED = 0xFFFFFFFF
ASFW_ANY = 0xFFFFFFFF
UOI_NAME = 2


class InstanceError(RuntimeError):
    """A native IPC or desktop-identity operation could not be completed."""


class Win32InstanceAPI:
    """Small injectable adapter. Borrowed desktop/station handles are not closed."""

    def __init__(self, kernel=None, user=None):
        if kernel is None or user is None:
            if sys.platform != "win32":
                raise InstanceError("Meeting HUD instance management requires Windows.")
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel, self.user = kernel, user
        signatures = (
            (kernel.CreateEventW, [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            (kernel.OpenEventW, [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            (kernel.CreateMutexW, [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            (kernel.OpenMutexW, [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            (kernel.SetEvent, [wintypes.HANDLE], wintypes.BOOL),
            (kernel.WaitForSingleObject, [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            (kernel.CloseHandle, [wintypes.HANDLE], wintypes.BOOL),
            (kernel.GetCurrentThreadId, [], wintypes.DWORD),
            (user.GetProcessWindowStation, [], wintypes.HANDLE),
            (user.GetThreadDesktop, [wintypes.DWORD], wintypes.HANDLE),
            (user.GetUserObjectInformationW, [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                             wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            (user.AllowSetForegroundWindow, [wintypes.DWORD], wintypes.BOOL),
        )
        for function, arguments, result in signatures:
            function.argtypes, function.restype = arguments, result

    @staticmethod
    def _failure(operation):
        return InstanceError(f"{operation} failed (Win32 {ctypes.get_last_error()}).")

    def _object_name(self, handle):
        required = wintypes.DWORD()
        ctypes.set_last_error(0)
        success = self.user.GetUserObjectInformationW(handle, UOI_NAME, None, 0, ctypes.byref(required))
        error = ctypes.get_last_error()
        if not success and error != ERROR_INSUFFICIENT_BUFFER:
            raise self._failure("Reading the Windows desktop identity")
        if not 0 < required.value <= 65536:
            raise InstanceError("Windows returned an invalid desktop identity size.")
        characters = (required.value + ctypes.sizeof(ctypes.c_wchar) - 1) // ctypes.sizeof(ctypes.c_wchar)
        buffer = ctypes.create_unicode_buffer(characters)
        if not self.user.GetUserObjectInformationW(handle, UOI_NAME, buffer, ctypes.sizeof(buffer), ctypes.byref(required)):
            raise self._failure("Reading the Windows desktop identity")
        if not buffer.value:
            raise InstanceError("Windows returned an empty desktop identity.")
        return buffer.value

    def desktop_identity(self):
        station = self.user.GetProcessWindowStation()
        if not station:
            raise self._failure("GetProcessWindowStation")
        desktop = self.user.GetThreadDesktop(self.kernel.GetCurrentThreadId())
        if not desktop:
            raise self._failure("GetThreadDesktop")
        return self._object_name(station), self._object_name(desktop)

    def create_event(self, name):
        handle = self.kernel.CreateEventW(None, False, False, name)
        if not handle:
            raise self._failure("CreateEventW")
        return handle

    def create_mutex(self, name):
        ctypes.set_last_error(0)
        handle = self.kernel.CreateMutexW(None, False, name)
        error = ctypes.get_last_error()  # Must precede every other native call.
        if not handle:
            raise InstanceError(f"CreateMutexW failed (Win32 {error}).")
        return handle, error == ERROR_ALREADY_EXISTS

    def _open(self, function, name):
        ctypes.set_last_error(0)
        handle = function(SYNCHRONIZE, False, name)
        error = ctypes.get_last_error()
        if not handle and error != ERROR_FILE_NOT_FOUND:
            raise InstanceError(f"Opening the HUD restore channel failed (Win32 {error}).")
        return handle or None

    def open_event(self, name):
        return self._open(self.kernel.OpenEventW, name)

    def open_mutex(self, name):
        return self._open(self.kernel.OpenMutexW, name)

    def signal(self, handle):
        # The launching process may grant activation permission after a user click.
        # Windows can deny or later revoke it; restoring visibility still works.
        self.user.AllowSetForegroundWindow(ASFW_ANY)
        if not self.kernel.SetEvent(handle):
            raise self._failure("SetEvent")

    def poll(self, handle):
        result = self.kernel.WaitForSingleObject(handle, 0)
        if result == WAIT_OBJECT_0:
            return True
        if result == WAIT_TIMEOUT:
            return False
        if result == WAIT_FAILED:
            raise self._failure("WaitForSingleObject")
        raise InstanceError(f"Unexpected restore-event wait result ({result}).")

    def close(self, handle):
        if not self.kernel.CloseHandle(handle):
            raise self._failure("CloseHandle")


def instance_names(project_path, desktop_identity):
    """Local\\ is session-scoped; the digest adds installation and desktop scope."""
    station, desktop = desktop_identity
    if not isinstance(station, str) or not station or not isinstance(desktop, str) or not desktop:
        raise InstanceError("Cannot determine the Windows desktop identity.")
    identity = [str(Path(project_path).resolve()).casefold(), station.casefold(), desktop.casefold()]
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode("ascii")).hexdigest()[:32]
    return "Local\\MeetingHUD_Instance_" + digest, "Local\\MeetingHUD_Restore_" + digest


class _Handles:
    def close(self):
        failure = None
        for attribute in ("_mutex", "_event"):
            handle = getattr(self, attribute, None)
            setattr(self, attribute, None)
            if handle is not None:
                try:
                    self.api.close(handle)
                except InstanceError as exc:
                    failure = failure or exc
        if failure is not None:
            raise failure

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is None:
            self.close()
        else:
            with suppress(InstanceError):
                self.close()
        return False


class SupervisorInstance(_Handles):
    """Context retained by launcher.pyw across every child exit-code-75 restart."""

    def __init__(self, project_path, api=None):
        self.api = api if api is not None else Win32InstanceAPI()
        self._event = self._mutex = None
        self.already_running = False
        try:
            mutex_name, event_name = instance_names(project_path, self.api.desktop_identity())
            # All contenders create/open the same event before electing an owner.
            # Opening an existing event does not erase a queued restore request.
            self._event = self.api.create_event(event_name)
            self._mutex, self.already_running = self.api.create_mutex(mutex_name)
        except Exception:
            with suppress(InstanceError):
                self.close()
            raise

    def signal_restore(self):
        if self._event is None:
            raise InstanceError("The HUD restore channel is closed.")
        self.api.signal(self._event)


class RestoreListener(_Handles):
    """GUI-side nonblocking poll. Missing supervisor means a direct debug launch."""

    def __init__(self, project_path, api=None):
        self.api = api if api is not None else Win32InstanceAPI()
        self._event = self._mutex = None
        try:
            mutex_name, event_name = instance_names(project_path, self.api.desktop_identity())
            self._event = self.api.open_event(event_name)
            if self._event is not None:
                # Retain the presence marker even if the launcher unexpectedly exits.
                self._mutex = self.api.open_mutex(mutex_name)
                if self._mutex is None:
                    self.close()
        except Exception:
            with suppress(InstanceError):
                self.close()
            raise

    def poll(self) -> bool:
        return self.api.poll(self._event) if self._event is not None else False
