"""Discover and preload Windows NVIDIA runtimes without importing Whisper or Torch.

Explicit HUD_CUDA_BIN entries take precedence over installed NVIDIA wheels, then
CUDA_PATH and PATH. Only absolute, existing directories are considered. Wheel
locations come from installed distribution metadata rather than arbitrary sys.path
entries. Absolute DLL paths make the selected core runtime visible in diagnostics.
"""
from __future__ import annotations

import ctypes
from dataclasses import asdict, dataclass
from importlib import metadata
import os
from pathlib import Path
import sys
from typing import Callable, Mapping

NVIDIA_PACKAGES = ("nvidia-cublas-cu12", "nvidia-cudnn-cu12", "nvidia-cuda-nvrtc-cu12")
CORE_DLLS = ("cublas64_12.dll", "cudnn64_9.dll")


@dataclass(frozen=True)
class RuntimeDirectory:
    path: str
    origin: str


class CudaRuntimeError(RuntimeError):
    """Actionable runtime setup error without API keys or transcript text."""


def discover_directories(explicit: str | None = None,
                         environment: Mapping[str, str] | None = None,
                         distribution_lookup: Callable | None = None) -> list[RuntimeDirectory]:
    environment = os.environ if environment is None else environment
    distribution_lookup = metadata.distribution if distribution_lookup is None else distribution_lookup
    found: list[RuntimeDirectory] = []
    seen = set()

    def add(value, origin):
        path = Path(value).expanduser()
        if not path.is_absolute() or not path.is_dir():
            return
        path = path.resolve()
        key = str(path).casefold()
        if key not in seen:
            seen.add(key)
            found.append(RuntimeDirectory(str(path), origin))

    for folder in (explicit or "").split(os.pathsep):
        if folder.strip():
            add(folder.strip(), "HUD_CUDA_BIN")
    for package in NVIDIA_PACKAGES:
        try:
            distribution = distribution_lookup(package)
        except metadata.PackageNotFoundError:
            continue
        for item in distribution.files or ():
            if str(item).lower().endswith(".dll"):
                add(Path(distribution.locate_file(item)).parent, f"pip:{package}")
    cuda_path = environment.get("CUDA_PATH")
    if cuda_path:
        add(Path(cuda_path) / "bin", "CUDA_PATH")
    for folder in environment.get("PATH", "").split(os.pathsep):
        if folder.strip():
            add(folder.strip(), "PATH")
    return found


def selected_libraries(directories: list[RuntimeDirectory]) -> list[Path]:
    """Select each core explicitly, then include its colocated helper libraries."""
    cores = []
    missing = []
    for filename in CORE_DLLS:
        match = next((Path(item.path) / filename for item in directories
                      if (Path(item.path) / filename).is_file()), None)
        if match is None:
            missing.append(filename)
        else:
            cores.append(match)
    if missing:
        raise CudaRuntimeError(
            "Missing " + ", ".join(missing) + ". Install requirements-gpu.txt in this venv "
            "or set HUD_CUDA_BIN to your CUDA 12/cuDNN 9 DLL directories.")
    blas, cudnn = cores
    result = []
    # Some native libraries load NVRTC by basename at runtime. Preload the
    # selected absolute path as well as registering its directory, so that this
    # also works for callers using legacy LoadLibrary search behavior.
    nvrtc = next((Path(item.path) / "nvrtc64_120_0.dll" for item in directories
                  if (Path(item.path) / "nvrtc64_120_0.dll").is_file()), None)
    if nvrtc is not None:
        result.extend(sorted(nvrtc.parent.glob("nvrtc-builtins64_*.dll")))
        result.append(nvrtc)
    blas_lt = blas.with_name("cublasLt64_12.dll")
    if blas_lt.is_file():
        result.append(blas_lt)
    result.extend((blas, cudnn))
    # Discover the installed cuDNN components instead of requiring an unrelated
    # hardcoded component set. The actual model warm-up validates the runtime.
    result.extend(path for path in sorted(cudnn.parent.glob("cudnn*64_9.dll"))
                  if path != cudnn)
    return result


class WindowsCudaRuntime:
    def __init__(self, explicit: str | None = None):
        self.explicit = explicit
        self.directories: list[RuntimeDirectory] = []
        self.loaded_files: list[str] = []
        self._directory_handles = []
        self._libraries = []

    def prepare(self, *, loader=None, add_directory=None) -> dict:
        if sys.platform != "win32":
            raise CudaRuntimeError("This CUDA runtime discovery helper targets Windows x64.")
        if self.loaded_files:
            return self.report()
        self.directories = discover_directories(self.explicit)
        files = selected_libraries(self.directories)
        loader = ctypes.WinDLL if loader is None else loader
        add_directory = os.add_dll_directory if add_directory is None else add_directory
        try:
            # Retain directory handles: otherwise Python removes the search paths.
            for directory in self.directories:
                try:
                    self._directory_handles.append(add_directory(directory.path))
                except OSError as exc:
                    raise CudaRuntimeError(
                        f"Cannot register DLL directory {directory.path} "
                        f"(Windows error {getattr(exc, 'winerror', None) or exc.errno}).") from None
            for path in files:
                try:
                    self._libraries.append(loader(str(path)))
                except OSError as exc:
                    code = getattr(exc, "winerror", None) or exc.errno or "unknown"
                    raise CudaRuntimeError(
                        f"Found {path.name}, but Windows could not load it (error {code}). "
                        "Check its dependent DLLs, the x64 Visual C++ runtime, and the NVIDIA driver. "
                        "Run main.py --diagnose for the selected paths.") from None
                self.loaded_files.append(str(path))
            return self.report()
        except Exception:
            self.close()
            raise

    def report(self) -> dict:
        return {"directories": [asdict(item) for item in self.directories],
                "loaded_libraries": list(self.loaded_files)}

    def close(self) -> None:
        self._libraries.clear()
        self.loaded_files.clear()
        for handle in reversed(self._directory_handles):
            handle.close()
        self._directory_handles.clear()
