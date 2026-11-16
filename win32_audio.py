"""Core Audio default-endpoint IDs; COM is owned by the capture QThread."""
from __future__ import annotations
import ctypes
import sys
import uuid


class GUID(ctypes.Structure):
    _fields_ = [("data1", ctypes.c_uint32), ("data2", ctypes.c_uint16),
                ("data3", ctypes.c_uint16), ("data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, value: str) -> "GUID":
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class EndpointWatcher:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("Core Audio requires Windows.")
        self._ole = ctypes.WinDLL("ole32")
        self._ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self._ole.CoInitializeEx.restype = ctypes.c_long
        self._ole.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p,
                                               ctypes.c_uint32, ctypes.POINTER(GUID),
                                               ctypes.POINTER(ctypes.c_void_p)]
        self._ole.CoCreateInstance.restype = ctypes.c_long
        self._ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        self._ole.CoTaskMemFree.restype = None
        self._ole.CoUninitialize.argtypes = []
        self._ole.CoUninitialize.restype = None
        self._owned_com = False
        self._enumerator = ctypes.c_void_p()
        hr = self._ole.CoInitializeEx(None, 0)
        if hr < 0:
            raise OSError(f"Core Audio COM initialization failed: {hr:#x}")
        self._owned_com = True
        clsid = GUID.parse("BCDE0395-E52F-467C-8E3D-C4579291692E")
        iid = GUID.parse("A95664D2-9614-4F35-A746-DE8DB63617E6")
        hr = self._ole.CoCreateInstance(ctypes.byref(clsid), None, 1,
                                        ctypes.byref(iid), ctypes.byref(self._enumerator))
        if hr < 0:
            self.close()
            raise OSError(f"Core Audio enumeration failed: {hr:#x}")

    @staticmethod
    def _method(pointer: ctypes.c_void_p, index: int, restype, *argtypes):
        vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])

    def _id(self, flow: int) -> str | None:
        endpoint = ctypes.c_void_p()
        get_default = self._method(self._enumerator, 4, ctypes.c_long,
                                   ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))
        hr = get_default(self._enumerator, flow, 0, ctypes.byref(endpoint))
        if hr < 0:
            return None
        text = ctypes.c_void_p()
        try:
            get_id = self._method(endpoint, 5, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p))
            hr = get_id(endpoint, ctypes.byref(text))
            if hr < 0:
                raise OSError(f"Core Audio GetId failed: {hr:#x}")
            return ctypes.wstring_at(text)
        finally:
            if text.value:
                self._ole.CoTaskMemFree(text)
            self._method(endpoint, 2, ctypes.c_ulong)(endpoint)

    def defaults(self) -> tuple[str | None, str | None]:
        return self._id(0), self._id(1)

    def close(self) -> None:
        if self._enumerator.value:
            self._method(self._enumerator, 2, ctypes.c_ulong)(self._enumerator)
            self._enumerator = ctypes.c_void_p()
        if self._owned_com:
            self._ole.CoUninitialize()
            self._owned_com = False
