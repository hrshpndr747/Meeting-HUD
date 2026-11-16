"""Native initialization order. This module must never import Qt."""
import importlib
import sys


class NativeStartupError(RuntimeError):
    pass


def preload_onnx() -> None:
    """Apply the ONNX-before-Qt workaround verified in the user's Windows report."""
    if sys.platform != "win32" or "onnxruntime" in sys.modules:
        return
    try:
        importlib.import_module("onnxruntime")
    except Exception as exc:
        raise NativeStartupError(
            f"ONNX Runtime could not initialize before Qt ({type(exc).__name__}). "
            "Repair the venv from requirements.txt and the x64 Visual C++ runtime. "
            "See logs/startup.log and run_debug.bat."
        ) from None
