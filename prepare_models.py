"""Download only configured local models, explicitly, before the first meeting."""
from __future__ import annotations
import shutil
import subprocess
import sys
from config import AppConfig


def prepare(config):
    selected = set()
    if config.backend == "local":
        selected.add(config.model)
    if config.dictation_backend == "local":
        selected.add(config.dictation_model)
    if config.offline and selected:
        raise ValueError("Disable HUD_OFFLINE while preparing uncached models.")
    if selected:
        from faster_whisper.utils import download_model
        from pathlib import Path
        for model in sorted(selected):
            if Path(model).is_dir():
                print("Using the configured local model directory.", flush=True)
                continue
            print(f"Downloading selected Whisper model: {model}", flush=True)
            download_model(model, cache_dir=config.model_cache)
    if config.text_translator == "ollama":
        executable = shutil.which("ollama")
        if not executable:
            raise RuntimeError("Install and start Ollama, then run prepare_models.bat again.")
        print(f"Preparing selected local English model: {config.ollama_model}", flush=True)
        # Argument list, no shell. Model validation rejects cloud tags.
        result = subprocess.run([executable, "pull", config.ollama_model], check=False)
        if result.returncode:
            raise RuntimeError("Ollama pull failed. Start Ollama, check the model name and retry.")
    print("Selected model downloads completed. Run run_debug.bat if startup still fails.")


if __name__ == "__main__":
    try:
        prepare(AppConfig.from_env())
    except Exception as exc:
        print(str(exc) if isinstance(exc, (ValueError, RuntimeError)) else
              f"Model preparation failed ({type(exc).__name__}). Check internet access and disk space.", file=sys.stderr)
        raise SystemExit(1)
