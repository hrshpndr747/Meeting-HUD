import ctypes
from ctypes import wintypes
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from support import APP, wait_for
from PyQt6.QtCore import Qt
from pipeline import Mode, Source, SpeechResult
from stealth_win32 import CaptureGuard, HOTKEY_ID, ProtectionError, WDA_EXCLUDEFROMCAPTURE
from ui_overlay import Overlay


class FakeNative:
    def __init__(self):
        self.affinity = WDA_EXCLUDEFROMCAPTURE
        self.composition = True
        self.set_ok = self.get_ok = self.hotkey_ok = True
        self.set_calls, self.registered, self.unregistered = [], [], []

    def DwmIsCompositionEnabled(self, output):
        ctypes.cast(output, ctypes.POINTER(wintypes.BOOL))[0] = self.composition
        return 0

    def SetWindowDisplayAffinity(self, hwnd, affinity):
        self.set_calls.append((hwnd, affinity))
        return self.set_ok

    def GetWindowDisplayAffinity(self, hwnd, output):
        ctypes.cast(output, ctypes.POINTER(wintypes.DWORD))[0] = self.affinity
        return self.get_ok

    def RegisterHotKey(self, hwnd, identifier, modifiers, key):
        self.registered.append((hwnd, identifier, modifiers, key))
        return self.hotkey_ok

    def UnregisterHotKey(self, hwnd, identifier):
        self.unregistered.append((hwnd, identifier))
        return True


class NativeContractTests(unittest.TestCase):
    def setUp(self):
        self.native = FakeNative()
        self.guard = CaptureGuard.__new__(CaptureGuard)
        self.guard._user32 = self.guard._dwmapi = self.native
        self.guard._hwnd = None
        for name, value in (("get_last_error", lambda: 5), ("set_last_error", lambda code: None)):
            patcher = patch.object(ctypes, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.guard.close)

    def test_protect_readback_hotkey_rebind_and_cleanup(self):
        self.guard.protect(100)
        self.guard.protect(100)
        self.guard.protect(200)
        self.assertTrue(all(affinity == 0x11 for _, affinity in self.native.set_calls))
        self.assertEqual([call[0] for call in self.native.registered], [100, 200])
        self.assertEqual(self.native.unregistered, [(100, HOTKEY_ID)])
        self.guard.close()
        self.assertEqual(self.native.unregistered[-1], (200, HOTKEY_ID))

    def test_set_failure_never_registers_hotkey(self):
        self.native.set_ok = False
        with self.assertRaisesRegex(ProtectionError, "SetWindowDisplayAffinity"):
            self.guard.protect(100)
        self.assertEqual(self.native.registered, [])

    def test_affinity_mismatch_and_dwm_failure_block_display(self):
        self.native.affinity = 1
        with self.assertRaisesRegex(ProtectionError, "not 0x11"):
            self.guard.protect(100)
        self.native.composition = False
        with self.assertRaisesRegex(ProtectionError, "composition"):
            self.guard.protect(100)

    def test_get_failure_and_unavailable_global_shortcut_are_errors(self):
        self.native.get_ok = False
        with self.assertRaisesRegex(ProtectionError, "GetWindowDisplayAffinity"):
            self.guard.protect(100)
        self.native.get_ok = True
        self.native.hotkey_ok = False
        with self.assertRaisesRegex(ProtectionError, "Ctrl\\+Shift\\+H"):
            self.guard.protect(100)
        self.assertIsNone(self.guard._hwnd)


class FakeGuard:
    def __init__(self):
        self.protected = []
        self.fail = self.closed = False

    def protect(self, hwnd):
        if self.fail:
            raise ProtectionError("Test affinity failure")
        self.protected.append(hwnd)

    def verify(self, hwnd):
        if self.fail:
            raise ProtectionError("Test affinity failure")

    def close(self):
        self.closed = True


class OverlayTests(unittest.TestCase):
    def setUp(self):
        self.guard = FakeGuard()
        self.window = Overlay(self.guard)
        def cleanup():
            self.window.prepare_shutdown()
            self.window.deleteLater()
            APP.processEvents()
        self.addCleanup(cleanup)
        self.window.show()
        APP.processEvents()

    def result(self, text, mode=Mode.TRANSCRIPT):
        return SpeechResult(text, Source.MIC, mode, 1, 0.1, 0.4, 1)

    def test_layout_native_flags_and_protection_before_display(self):
        self.assertEqual((self.window.width(), self.window.height()), (600, 116))
        self.assertEqual(self.window.minimumSize().width(), 520)
        flags = self.window.windowFlags()
        self.assertTrue(flags & Qt.WindowType.FramelessWindowHint)
        self.assertTrue(flags & Qt.WindowType.WindowStaysOnTopHint)
        self.assertEqual(flags & Qt.WindowType.WindowType_Mask, Qt.WindowType.Tool)
        self.assertTrue(self.window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        self.assertGreaterEqual(len(self.guard.protected), 2)
        self.assertGreater(self.window.content.height(), 30)

    def test_plain_text_rendering_modes_and_latest_sentence_copy(self):
        self.window.add_result(self.result("<b>literal</b>"))
        self.assertIn("<b>literal</b>", self.window.content.toPlainText())
        self.window.select_mode(Mode.TRANSLATION)
        self.window.add_result(self.result("Old transcript"))
        self.assertEqual(self.window.content.toPlainText(), "")
        self.window.add_result(self.result("First sentence. Latest sentence!", Mode.TRANSLATION))
        self.window.copy_latest()
        copied = APP.clipboard().text()
        if copied:
            self.assertEqual(copied, "First sentence. Latest sentence!")
        self.assertEqual(self.window.latest_translation, "First sentence. Latest sentence!")
        sample = "ohh sorry brother I need this starting next month not right now I hope will still have it by next month."
        self.window.add_result(self.result(sample, Mode.TRANSLATION))
        self.window.copy_latest()
        copied = APP.clipboard().text()
        if copied:
            self.assertEqual(copied, sample)
        self.assertEqual(self.window.latest_translation, sample)
        self.window.select_mode(Mode.TRANSCRIPT)
        self.assertEqual(self.window.latest_translation, "")
        self.assertFalse(self.window.copy_button.isEnabled())

    def test_failed_affinity_hides_window_and_prevents_hotkey_reshow(self):
        failures = []
        self.window.protection_failed.connect(failures.append)
        self.guard.fail = True
        self.window._verify_protection()
        self.assertFalse(self.window.isVisible())
        self.window.toggle_visibility()
        self.assertFalse(self.window.isVisible())
        self.assertEqual(failures, ["Test affinity failure"])

    def test_visibility_toggle_resize_and_shutdown(self):
        self.window.toggle_visibility()
        self.assertFalse(self.window.isVisible())
        self.window.toggle_visibility()
        self.assertTrue(self.window.isVisible())
        self.window.resize(680, 220)
        APP.processEvents()
        self.assertEqual(self.window.grip.pos().x(), 665)
        self.window.prepare_shutdown()
        self.assertTrue(self.guard.closed)
        self.window.toggle_visibility()
        self.assertFalse(self.window.isVisible())

    def test_transcript_history_is_bounded(self):
        for index in range(230):
            self.window.add_result(self.result(f"Sentence {index}."))
        APP.processEvents()
        self.assertEqual(self.window.content.document().blockCount(), 200)
        self.assertNotIn("Sentence 0.", self.window.content.toPlainText())
        self.assertIn("Sentence 229.", self.window.content.toPlainText())


if __name__ == "__main__":
    unittest.main()
