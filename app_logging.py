"""File-backed startup diagnostics for pythonw and native faults, without transcripts."""
import faulthandler
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

_streams = []


def configure_logging():
    if _streams:
        return
    directory = Path(__file__).resolve().parent / "logs"
    directory.mkdir(exist_ok=True)
    for name in ("console.log", "native.log"):
        path = directory / name
        if path.exists() and path.stat().st_size > 2_000_000:
            path.replace(path.with_suffix(".previous.log"))
    console = (directory / "console.log").open("a", encoding="utf-8", buffering=1)
    native = (directory / "native.log").open("a", encoding="utf-8", buffering=1)
    _streams.extend((console, native))
    if sys.stdout is None:
        sys.stdout = console
    if sys.stderr is None:
        sys.stderr = console
    handlers = [RotatingFileHandler(directory / "startup.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")]
    if sys.stderr is not console:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", force=True)
    faulthandler.enable(native, all_threads=True)
