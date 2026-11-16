"""Read-only setup checks; native imports run in disposable child processes.

Reports contain package versions, device names and local DLL/model paths, but no
API key, glossary text, transcript, environment dump, or captured audio. A model
is loaded/downloaded only with the explicit --check-model option.
"""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
import struct
import subprocess
import sys
import tempfile
import time

from config import APP_VERSION, AppConfig
from cuda_runtime import CudaRuntimeError, WindowsCudaRuntime, discover_directories, selected_libraries

PROBE_PREFIX = "HUD_DIAG_JSON="
PROBES = ("cuda", "audio", "silero", "model", "ollama")


def package_report() -> dict:
    packages = {}
    for filename in ("requirements.txt", "requirements-gpu.txt"):
        path = Path(__file__).with_name(filename)
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"([A-Za-z0-9_.-]+)==([^;\s]+)", line)
            if match is None:
                continue
            name, expected = match.groups()
            try:
                installed = metadata.version(name)
            except metadata.PackageNotFoundError:
                installed = None
            packages[name] = {"expected": expected, "installed": installed,
                              "matches_pin": installed == expected,
                              "optional_gpu": filename == "requirements-gpu.txt"}
    return packages


def safe_configuration(config: AppConfig) -> dict:
    return {"backend": config.backend, "model": config.model, "device": config.device,
            "cpu_threads": config.cpu_threads,
            "translation_language": config.translation_language or "auto",
            "transcript_language": config.transcript_language or "auto",
            "min_chunk_ms": round(config.min_chunk_s * 1000),
            "max_chunk_ms": round(config.max_chunk_s * 1000),
            "pause_ms": round(config.pause_s * 1000), "energy_floor": config.energy_floor,
            "offline": config.offline, "model_cache": config.model_cache,
            "cuda_override_configured": bool(config.cuda_bin),
            "glossary_configured": bool(config.initial_prompt),
            "groq_key_configured": bool(config.groq_api_key),
            "gemini_key_configured": bool(config.gemini_api_key), "cloud_enabled": config.cloud_enabled,
            "dictation_backend": config.dictation_backend, "dictation_model": config.dictation_model,
            "text_translator": config.text_translator, "ollama_model": config.ollama_model,
            "text_model": config.text_model, "gemini_model": config.gemini_model,
            "max_record_s": config.max_record_s,
            "selected_microphone": config.microphone or "Windows default",
            "noise_suppression": config.noise_suppression,
            "rnnoise_installed": Path(__file__).with_name("native").joinpath("rnnoise.dll").is_file()}


def run_probe(name: str, timeout: float = 20.0) -> dict:
    if name not in PROBES:
        raise ValueError("Unknown diagnostic probe.")
    command = [sys.executable, "-u", str(Path(__file__).resolve()), "--probe", name]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=timeout,
                                cwd=Path(__file__).resolve().parent, creationflags=flags)
    except subprocess.TimeoutExpired:
        return {"status": "error", "reason": f"{name} probe timed out after {timeout:g}s.",
                "timed_out": True}
    except OSError as exc:
        return {"status": "error", "reason": f"Could not start {name} probe ({type(exc).__name__})."}
    if result.returncode == 0:
        for line in reversed(result.stdout.splitlines()):
            if line.startswith(PROBE_PREFIX):
                try:
                    value = json.loads(line[len(PROBE_PREFIX):])
                    if isinstance(value, dict) and "status" in value:
                        return value
                except (ValueError, TypeError):
                    break
    # Do not copy arbitrary native stderr or exception bodies into shared reports.
    return {"status": "error", "reason": f"{name} probe exited without a valid report.",
            "exit_code": result.returncode, "native_stderr_present": bool(result.stderr.strip())}


def _cuda_probe() -> dict:
    config = AppConfig.from_env()
    directories = discover_directories(config.cuda_bin)
    report = {"status": "ok", "directories": [asdict(item) for item in directories]}
    runtime = WindowsCudaRuntime(config.cuda_bin)
    try:
        try:
            report["selected_libraries"] = [str(path) for path in selected_libraries(directories)]
            report.update(runtime.prepare())
        except CudaRuntimeError as exc:
            report.update(status="error", runtime_error=str(exc))
        try:
            import ctranslate2
            count = ctranslate2.get_cuda_device_count()
            report["cuda_device_count"] = count
            if count:
                report["cuda_compute_types"] = sorted(ctranslate2.get_supported_compute_types("cuda"))
            else:
                report.update(status="error", device_error="No CUDA device detected; check the NVIDIA driver.")
            report["cpu_compute_types"] = sorted(ctranslate2.get_supported_compute_types("cpu"))
        except Exception as exc:
            report.update(status="error", ctranslate2_error=type(exc).__name__)
        return report
    finally:
        runtime.close()


def _audio_probe() -> dict:
    import pyaudiowpatch as pyaudio
    pa = pyaudio.PyAudio()
    report = {"status": "ok"}

    def describe(device):
        return {"name": str(device["name"]), "index": int(device["index"]),
                "input_channels": int(device["maxInputChannels"]),
                "sample_rate": int(device["defaultSampleRate"])}

    try:
        for source in ("loopback", "microphone"):
            try:
                if source == "loopback":
                    device = pa.get_default_wasapi_loopback()
                else:
                    from microphones import resolve_microphone
                    device = resolve_microphone(pa, pyaudio.paWASAPI, AppConfig.from_env().microphone)
                report[source] = describe(device)
            except Exception as exc:
                report.update(status="error")
                report[source] = {"error": type(exc).__name__, "reason": "Default WASAPI device unavailable."}
        return report
    finally:
        pa.terminate()


def _silero_probe() -> dict:
    from runtime_bootstrap import preload_onnx
    preload_onnx()
    import numpy as np
    from faster_whisper.vad import get_speech_timestamps
    started = time.monotonic()
    timestamps = get_speech_timestamps(np.zeros(16000, dtype=np.float32))
    return {"status": "ok" if not timestamps else "error", "silent_segments": len(timestamps),
            "warmup_seconds": round(time.monotonic() - started, 3)}


def _model_probe() -> dict:
    from runtime_bootstrap import preload_onnx
    preload_onnx()
    config = AppConfig.from_env()
    selected = []
    if config.backend == "local":
        selected.append(("live", config.model))
    if config.dictation_backend == "local":
        selected.append(("dictation", config.dictation_model))
    if not selected:
        return {"status": "skipped", "reason": "Model check is local-only; no cloud audio is uploaded."}
    from speech_engine import LocalWhisperBackend
    stages = {}
    for stage, model in selected:
        backend = LocalWhisperBackend(replace(config, backend="local", model=model), lambda message: None)
        started = time.monotonic()
        try:
            description = backend.load()
            stages[stage] = {"status": "warning" if backend.fallback_reason else "ok",
                            "description": description, "device": backend.device,
                            "fallback_reason": backend.fallback_reason,
                            "model_and_silero_warmup_seconds": round(time.monotonic() - started, 3)}
        finally:
            backend.close()
    return {"status": "warning" if any(s["status"] == "warning" for s in stages.values()) else "ok", "stages": stages}


def _ollama_probe() -> dict:
    config = AppConfig.from_env()
    if config.text_translator != "ollama":
        return {"status": "skipped", "reason": "A cloud text provider is selected; no API calls are made by diagnostics."}
    import httpx
    from network_io import request_json
    with httpx.Client(timeout=4, follow_redirects=False, trust_env=False) as client:
        code, data, _ = request_json(client, "POST", "http://127.0.0.1:11434/api/show",
                                     json={"model": config.ollama_model}, max_seconds=8)
    local = isinstance(data, dict) and not data.get("remote_host") and not data.get("remote_model")
    capable = isinstance(data, dict) and "completion" in data.get("capabilities", [])
    return {"status": "ok" if code == 200 and local and capable else "error",
            "model": config.ollama_model, "http_status": code,
            "local_model": bool(local and code == 200), "supports_text_generation": capable}


def probe_main(name: str) -> int:
    functions = {"cuda": _cuda_probe, "audio": _audio_probe,
                 "silero": _silero_probe, "model": _model_probe, "ollama": _ollama_probe}
    if name not in functions:
        return 2
    try:
        result = functions[name]()
    except Exception as exc:
        result = {"status": "error", "reason": f"{name} check failed ({type(exc).__name__})."}
    print(PROBE_PREFIX + json.dumps(result, ensure_ascii=True), flush=True)
    return 0


def build_report(check_model: bool = False) -> dict:
    report = {"app_version": APP_VERSION,
              "generated_at_utc": datetime.now(timezone.utc).isoformat(),
              "platform": sys.platform, "os_version": platform.version(),
              "python": platform.python_version(), "python_bits": struct.calcsize("P") * 8,
              "packages": package_report()}
    if sys.platform == "win32":
        report["windows_build"] = sys.getwindowsversion().build
    try:
        config = AppConfig.from_env()
        report["configuration"] = safe_configuration(config)
    except (ValueError, TypeError):
        report["configuration"] = {"error": "Invalid HUD_* configuration; check the settings in SETUP.md."}
    if sys.platform == "win32":
        report["cuda"] = run_probe("cuda")
        report["audio"] = run_probe("audio")
    else:
        report["cuda"] = {"status": "skipped", "reason": "Windows runtime discovery requires Windows."}
        report["audio"] = {"status": "skipped", "reason": "WASAPI requires Windows."}
    report["silero"] = run_probe("silero")
    report["ollama"] = run_probe("ollama", timeout=15)
    report["model"] = (run_probe("model", timeout=300.0) if check_model else
                       {"status": "skipped", "reason": "Use --check-model to load and warm the local model."})
    report["capture_exclusion"] = {
        "status": "not_tested", "reason": "Launch the real HUD and test each capture application on Windows."
    }
    report["notes"] = ["No audio is recorded by these diagnostic probes.",
                       "Model warm-up uses generated silence, not microphone or meeting audio.",
                       "Paths and device names may identify this PC; review before sharing.",
                       "Report creation succeeding does not mean every check passed."]
    return report


def write_report(path: str, report: dict) -> None:
    target = Path(path).expanduser().absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                         prefix=".hud-report-", suffix=".tmp", delete=False) as output:
            name = output.name
            json.dump(report, output, indent=2, ensure_ascii=True)
            output.write("\n")
        os.replace(name, target)
    finally:
        if name is not None and os.path.exists(name):
            os.unlink(name)


def diagnose(report_path: str | None = None, check_model: bool = False) -> int:
    report = build_report(check_model)
    print(json.dumps(report, indent=2, ensure_ascii=True))
    if report_path:
        try:
            write_report(report_path, report)
        except OSError as exc:
            print(f"Could not save diagnostic report ({type(exc).__name__}).", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--probe":
        raise SystemExit(probe_main(sys.argv[2]))
    raise SystemExit("Run: python main.py --diagnose [--check-model] [--report diagnostics.json]")
