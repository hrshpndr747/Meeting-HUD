"""Quiet, supervised Windows launcher. No Qt/native ML imports in this process."""
import ctypes
from ctypes import wintypes
from pathlib import Path
import subprocess
import sys

from instance_ipc import SupervisorInstance


def show_error(message):
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    user.MessageBoxW.restype = ctypes.c_int
    user.MessageBoxW(None, message, "Meeting HUD", 0x10)


def launch(argv=()):
    if any(arg not in ("--paused", "--smoke-ui") for arg in argv):
        raise ValueError("Unsupported launcher argument")
    root = Path(__file__).resolve().parent
    interpreter = root / ".venv" / "Scripts" / "pythonw.exe"
    with SupervisorInstance(root) as instance:
        if instance.already_running:
            instance.signal_restore()
            return 0
        if not interpreter.is_file():
            show_error("Run setup.bat first, or create .venv and install requirements.txt. See SETUP.md.")
            return 2
        logs = root / "logs"
        logs.mkdir(exist_ok=True)
        path = logs / "launcher.log"
        if path.exists() and path.stat().st_size > 2_000_000:
            path.replace(logs / "launcher.previous.log")
        with path.open("a", encoding="utf-8") as output:
            while True:
                result = subprocess.run([str(interpreter), str(root / "main.py"), *argv], cwd=root,
                                        stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if result.returncode != 75:
                    break
        if result.returncode:
            show_error(f"Meeting HUD stopped with error code {result.returncode}.\n\nDetails: {logs}\n\nUse run_debug.bat to troubleshoot. No console needs to stay open for normal use.")
        return result.returncode


if __name__ == "__main__":
    try:
        raise SystemExit(launch(sys.argv[1:]))
    except Exception as exc:
        show_error(f"Could not launch Meeting HUD ({type(exc).__name__}). Extract it to a writable folder and run setup.bat.")
        raise SystemExit(2)
