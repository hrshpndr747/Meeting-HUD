from __future__ import annotations
import os
import sys
import time

if sys.platform != "win32":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from runtime_bootstrap import preload_onnx
preload_onnx()
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)


def wait_for(predicate, seconds=3):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        APP.processEvents()
        QTest.qWait(5)
    APP.processEvents()
    if not predicate():
        raise AssertionError("Condition did not become true before the deadline.")
