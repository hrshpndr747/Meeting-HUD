from importlib import metadata
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cuda_runtime import CudaRuntimeError, RuntimeDirectory, WindowsCudaRuntime, discover_directories, selected_libraries


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def folder(self, name, files=()):
        path = self.root / name
        path.mkdir()
        for filename in files:
            (path / filename).write_bytes(b"test-only")
        return path

    def test_discovery_prioritizes_override_then_wheel_then_environment(self):
        override = self.folder("override")
        wheel = self.folder("wheel", ["cublas64_12.dll"])
        cuda = self.folder("cuda")
        (cuda / "bin").mkdir()
        path = self.folder("path")
        def lookup(name):
            if name != "nvidia-cublas-cu12":
                raise metadata.PackageNotFoundError(name)
            return SimpleNamespace(files=["cublas64_12.dll", "readme.txt"], locate_file=lambda item: wheel / item)
        directories = discover_directories(str(override), {"CUDA_PATH": str(cuda), "PATH": os.pathsep.join((".", str(path), str(override)))}, lookup)
        self.assertEqual([item.path for item in directories], list(map(str, (override, wheel, cuda / "bin", path))))
        self.assertEqual([item.origin for item in directories], ["HUD_CUDA_BIN", "pip:nvidia-cublas-cu12", "CUDA_PATH", "PATH"])

    def test_missing_distribution_and_relative_directories_are_ignored(self):
        lookup = Mock(side_effect=metadata.PackageNotFoundError("missing"))
        self.assertEqual(discover_directories("relative", {"PATH": ".", "CUDA_PATH": "relative"}, lookup), [])

    def test_nvrtc_wheel_directory_is_discovered_for_cublas_dependency(self):
        folder = self.folder("nvrtc", ["nvrtc64_120_0.dll"])
        def lookup(name):
            if name != "nvidia-cuda-nvrtc-cu12":
                raise metadata.PackageNotFoundError(name)
            return SimpleNamespace(files=["nvrtc64_120_0.dll"], locate_file=lambda item: folder / item)
        directories = discover_directories(environment={}, distribution_lookup=lookup)
        self.assertEqual(directories, [RuntimeDirectory(str(folder), "pip:nvidia-cuda-nvrtc-cu12")])

    def test_explicit_core_selection_and_colocated_components(self):
        override = self.folder("override", ["cublas64_12.dll", "cublasLt64_12.dll"])
        wheel = self.folder("wheel", ["cublas64_12.dll", "cudnn64_9.dll", "cudnn_ops64_9.dll", "cudnn64_8.dll"])
        chosen = selected_libraries([RuntimeDirectory(str(override), "override"), RuntimeDirectory(str(wheel), "wheel")])
        self.assertEqual(chosen, [override / "cublasLt64_12.dll", override / "cublas64_12.dll",
                                  wheel / "cudnn64_9.dll", wheel / "cudnn_ops64_9.dll"])

    def test_missing_core_message_explains_installation(self):
        folder = self.folder("incomplete", ["cublas64_12.dll"])
        with self.assertRaisesRegex(CudaRuntimeError, "Missing cudnn64_9.dll.*requirements-gpu.txt"):
            selected_libraries([RuntimeDirectory(str(folder), "test")])

    def test_nvrtc_and_its_builtins_preload_before_cublas(self):
        core = self.folder("core", ["cublas64_12.dll", "cudnn64_9.dll"])
        compiler = self.folder("compiler", ["nvrtc64_120_0.dll", "nvrtc-builtins64_128.dll"])
        chosen = selected_libraries([RuntimeDirectory(str(core), "test"), RuntimeDirectory(str(compiler), "test")])
        self.assertEqual(chosen, [compiler / "nvrtc-builtins64_128.dll", compiler / "nvrtc64_120_0.dll",
                                  core / "cublas64_12.dll", core / "cudnn64_9.dll"])

    def test_handles_remain_alive_until_close_and_prepare_is_idempotent(self):
        folder = self.folder("runtime", ["cublas64_12.dll", "cudnn64_9.dll"])
        handles, loaded = [], []
        def register(path):
            handle = SimpleNamespace(close=Mock())
            handles.append(handle)
            return handle
        def load(path):
            self.assertTrue(Path(path).is_absolute())
            self.assertTrue(all(not h.close.called for h in handles))
            loaded.append(path)
            return object()
        runtime = WindowsCudaRuntime()
        with patch("cuda_runtime.sys.platform", "win32"), patch("cuda_runtime.discover_directories", return_value=[RuntimeDirectory(str(folder), "test")]):
            report = runtime.prepare(loader=load, add_directory=register)
            self.assertEqual(len(report["loaded_libraries"]), 2)
            self.assertEqual(runtime.prepare(loader=load, add_directory=register), report)
        self.assertEqual(len(loaded), 2)
        self.assertFalse(handles[0].close.called)
        runtime.close()
        handles[0].close.assert_called_once()
        self.assertEqual(runtime.report()["loaded_libraries"], [])
        runtime.close()
        handles[0].close.assert_called_once()

    def test_dependency_load_failure_closes_handles_and_reports_error_code(self):
        folder = self.folder("runtime", ["cublas64_12.dll", "cudnn64_9.dll"])
        handle = SimpleNamespace(close=Mock())
        error = OSError("sensitive native detail")
        error.winerror = 126
        runtime = WindowsCudaRuntime()
        with patch("cuda_runtime.sys.platform", "win32"), patch("cuda_runtime.discover_directories", return_value=[RuntimeDirectory(str(folder), "test")]):
            with self.assertRaisesRegex(CudaRuntimeError, "error 126") as caught:
                runtime.prepare(loader=Mock(side_effect=error), add_directory=lambda _: handle)
        self.assertNotIn("sensitive native detail", str(caught.exception))
        handle.close.assert_called_once()
        self.assertEqual(runtime.loaded_files, [])

    def test_directory_registration_failure_closes_previous_handles(self):
        first = self.folder("first", ["cublas64_12.dll"])
        second = self.folder("second", ["cudnn64_9.dll"])
        handle = SimpleNamespace(close=Mock())
        register = Mock(side_effect=[handle, OSError(5, "access denied")])
        runtime = WindowsCudaRuntime()
        directories = [RuntimeDirectory(str(first), "test"), RuntimeDirectory(str(second), "test")]
        with patch("cuda_runtime.sys.platform", "win32"), patch("cuda_runtime.discover_directories", return_value=directories):
            with self.assertRaisesRegex(CudaRuntimeError, "Cannot register DLL directory"):
                runtime.prepare(loader=Mock(), add_directory=register)
        handle.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
