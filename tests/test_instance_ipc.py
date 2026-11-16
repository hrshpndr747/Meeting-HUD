"""Portable contracts for the Windows instance coordinator and quiet supervisor."""
import ctypes
from ctypes import wintypes
import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from instance_ipc import (ERROR_ALREADY_EXISTS, ERROR_FILE_NOT_FOUND, InstanceError,
                          RestoreListener, SupervisorInstance, WAIT_FAILED,
                          WAIT_OBJECT_0, WAIT_TIMEOUT, Win32InstanceAPI, instance_names)


class ObjectRegistry:
    def __init__(self):
        self.objects, self.handles, self.operations = {}, {}, []
        self.next_handle = 1 << 40

    def open(self, kind, name, create):
        key = kind, name
        existed = key in self.objects
        if not existed and not create:
            return None, False
        obj = self.objects.setdefault(key, {"signaled": False, "refs": 0})
        obj["refs"] += 1
        self.next_handle += 1
        self.handles[self.next_handle] = key
        return self.next_handle, existed


class FakeAPI:
    def __init__(self, registry=None, desktop="Default"):
        self.registry = registry or ObjectRegistry()
        self.desktop = desktop
        self.closed = []

    def desktop_identity(self):
        return "WinSta0", self.desktop

    def create_event(self, name):
        self.registry.operations.append("event")
        return self.registry.open("event", name, True)[0]

    def create_mutex(self, name):
        self.registry.operations.append("mutex")
        return self.registry.open("mutex", name, True)

    def open_event(self, name):
        return self.registry.open("event", name, False)[0]

    def open_mutex(self, name):
        return self.registry.open("mutex", name, False)[0]

    def signal(self, handle):
        self.registry.objects[self.registry.handles[handle]]["signaled"] = True

    def poll(self, handle):
        obj = self.registry.objects[self.registry.handles[handle]]
        result = obj["signaled"]
        obj["signaled"] = False
        return result

    def close(self, handle):
        self.closed.append(handle)
        key = self.registry.handles.pop(handle)
        obj = self.registry.objects[key]
        obj["refs"] -= 1
        if obj["refs"] == 0:
            del self.registry.objects[key]


class CoordinatorTests(unittest.TestCase):
    def test_event_precedes_election_and_queued_restore_survives_child_restart(self):
        api = FakeAPI()
        with SupervisorInstance(Path.cwd(), api) as owner:
            self.assertFalse(owner.already_running)
            self.assertEqual(api.registry.operations, ["event", "mutex"])
            child = RestoreListener(Path.cwd(), FakeAPI(api.registry))
            child.close()
            with SupervisorInstance(Path.cwd(), FakeAPI(api.registry)) as duplicate:
                self.assertTrue(duplicate.already_running)
                duplicate.signal_restore()
            with RestoreListener(Path.cwd(), FakeAPI(api.registry)) as restarted:
                self.assertTrue(restarted.poll())
                self.assertFalse(restarted.poll(), "Auto-reset must consume a restore once")
        self.assertEqual(api.registry.handles, {})
        self.assertEqual(api.registry.objects, {})

    def test_orphaned_gui_keeps_presence_marker_until_it_closes(self):
        api = FakeAPI()
        owner = SupervisorInstance(Path.cwd(), api)
        child = RestoreListener(Path.cwd(), FakeAPI(api.registry))
        owner.close()
        with SupervisorInstance(Path.cwd(), FakeAPI(api.registry)) as duplicate:
            self.assertTrue(duplicate.already_running)
            duplicate.signal_restore()
        self.assertTrue(child.poll())
        child.close()
        with SupervisorInstance(Path.cwd(), FakeAPI(api.registry)) as replacement:
            self.assertFalse(replacement.already_running)

    def test_desktops_and_installations_do_not_share_instances(self):
        api = FakeAPI()
        with SupervisorInstance(Path.cwd(), api):
            with SupervisorInstance(Path.cwd(), FakeAPI(api.registry, "exebox-isolated")) as other_desktop:
                self.assertFalse(other_desktop.already_running)
            with SupervisorInstance(Path.cwd() / "other-install", FakeAPI(api.registry)) as other_install:
                self.assertFalse(other_install.already_running)
        names = instance_names(Path.cwd(), ("WinSta0", "Default"))
        self.assertNotEqual(names, instance_names(Path.cwd(), ("OtherStation", "Default")))

    def test_debug_launch_without_supervisor_is_noop_and_close_is_idempotent(self):
        api = FakeAPI()
        listener = RestoreListener(Path.cwd(), api)
        self.assertFalse(listener.poll())
        listener.close()
        listener.close()
        self.assertFalse(listener.poll())
        self.assertEqual(api.closed, [])

    def test_identity_and_partial_election_failure_are_explicit_and_cleaned(self):
        api = FakeAPI()
        with patch.object(api, "desktop_identity", side_effect=InstanceError("identity failed")):
            with self.assertRaisesRegex(InstanceError, "identity failed"):
                SupervisorInstance(Path.cwd(), api)
        self.assertEqual(api.registry.handles, {})
        with patch.object(api, "create_mutex", side_effect=InstanceError("mutex failed")):
            with self.assertRaisesRegex(InstanceError, "mutex failed"):
                SupervisorInstance(Path.cwd(), api)
        self.assertEqual(api.registry.handles, {})
        with self.assertRaises(InstanceError):
            instance_names(Path.cwd(), ("", "Default"))

    def test_listener_does_not_attach_to_event_without_an_owner_mutex(self):
        api = FakeAPI()
        _, name = instance_names(Path.cwd(), api.desktop_identity())
        event = api.create_event(name)
        listener = RestoreListener(Path.cwd(), api)
        self.assertFalse(listener.poll())
        listener.close()
        api.close(event)
        self.assertEqual(api.registry.handles, {})


class NativeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.kernel = SimpleNamespace(**{name: Mock() for name in (
            "CreateEventW", "OpenEventW", "CreateMutexW", "OpenMutexW", "SetEvent",
            "WaitForSingleObject", "CloseHandle", "GetCurrentThreadId")})
        self.user = SimpleNamespace(**{name: Mock() for name in (
            "GetProcessWindowStation", "GetThreadDesktop", "GetUserObjectInformationW",
            "AllowSetForegroundWindow")})
        self.error = 0
        get_error = patch.object(ctypes, "get_last_error", lambda: self.error, create=True)
        set_error = patch.object(ctypes, "set_last_error", lambda value: setattr(self, "error", value), create=True)
        get_error.start()
        set_error.start()
        self.addCleanup(get_error.stop)
        self.addCleanup(set_error.stop)
        self.api = Win32InstanceAPI(self.kernel, self.user)

    def test_all_native_handle_apis_are_typed_and_preserve_64_bit_values(self):
        for name in ("CreateEventW", "OpenEventW", "CreateMutexW", "OpenMutexW"):
            self.assertIs(getattr(self.kernel, name).restype, wintypes.HANDLE)
            self.assertIsNotNone(getattr(self.kernel, name).argtypes)
        for name in ("GetProcessWindowStation", "GetThreadDesktop"):
            self.assertIs(getattr(self.user, name).restype, wintypes.HANDLE)
        value = (1 << 45) + 19
        self.kernel.CreateEventW.return_value = value
        self.assertEqual(self.api.create_event("name"), value)
        self.kernel.CreateEventW.assert_called_once_with(None, False, False, "name")

    def test_mutex_election_clears_stale_last_error_and_reads_creation_result(self):
        self.error = ERROR_ALREADY_EXISTS
        self.kernel.CreateMutexW.return_value = 1 << 42
        self.assertEqual(self.api.create_mutex("name"), (1 << 42, False))
        def existing(*args):
            self.error = ERROR_ALREADY_EXISTS
            return 1 << 43
        self.kernel.CreateMutexW.side_effect = existing
        self.assertEqual(self.api.create_mutex("name"), (1 << 43, True))

    def test_missing_event_is_allowed_but_access_denied_is_not_suppressed(self):
        def missing(*args):
            self.error = ERROR_FILE_NOT_FOUND
            return None
        self.kernel.OpenEventW.side_effect = missing
        self.assertIsNone(self.api.open_event("name"))
        def denied(*args):
            self.error = 5
            return None
        self.kernel.OpenEventW.side_effect = denied
        with self.assertRaisesRegex(InstanceError, "Win32 5"):
            self.api.open_event("name")

    def test_wait_errors_are_not_mistaken_for_restore_and_activation_denial_is_nonfatal(self):
        self.kernel.WaitForSingleObject.side_effect = [WAIT_TIMEOUT, WAIT_OBJECT_0, WAIT_FAILED]
        self.assertFalse(self.api.poll(123))
        self.assertTrue(self.api.poll(123))
        with self.assertRaises(InstanceError):
            self.api.poll(123)
        self.user.AllowSetForegroundWindow.return_value = False
        self.kernel.SetEvent.return_value = True
        self.api.signal(123)
        self.kernel.SetEvent.assert_called_once_with(123)


class LauncherTests(unittest.TestCase):
    @staticmethod
    def load_launcher():
        path = Path(__file__).resolve().parent.parent / "launcher.pyw"
        loader = importlib.machinery.SourceFileLoader("hud_launcher_test", str(path))
        module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(module)
        return module

    def test_duplicate_shortcut_signals_restore_without_modal_or_child_spawn(self):
        launcher = self.load_launcher()
        owner = Mock(already_running=True)
        owner.__enter__ = Mock(return_value=owner)
        owner.__exit__ = Mock(return_value=False)
        with patch.object(launcher, "SupervisorInstance", return_value=owner), \
                patch.object(launcher, "show_error") as error, patch.object(launcher.subprocess, "run") as run:
            self.assertEqual(launcher.launch(), 0)
        owner.signal_restore.assert_called_once_with()
        error.assert_not_called()
        run.assert_not_called()

    def test_supervisor_retains_owner_across_exit_75_restart_loop(self):
        launcher = self.load_launcher()
        api = FakeAPI()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            interpreter = root / ".venv" / "Scripts" / "pythonw.exe"
            interpreter.parent.mkdir(parents=True)
            interpreter.touch()
            owner = SupervisorInstance(root, api)
            results = iter((75, 0))
            def run(*args, **kwargs):
                self.assertEqual(len(api.registry.handles), 2)
                owner.signal_restore()
                return SimpleNamespace(returncode=next(results))
            with patch.object(launcher, "__file__", str(root / "launcher.pyw")), \
                    patch.object(launcher, "SupervisorInstance", return_value=owner), \
                    patch.object(launcher.subprocess, "run", side_effect=run) as child, \
                    patch.object(launcher, "show_error") as error:
                self.assertEqual(launcher.launch(), 0)
                self.assertEqual(child.call_count, 2)
                error.assert_not_called()
            self.assertEqual(api.registry.handles, {})


if __name__ == "__main__":
    unittest.main()
