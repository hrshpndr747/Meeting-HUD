"""Install only the native RNNoise DLL from a pinned, hash-verified PyPI wheel."""
from __future__ import annotations
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile

FILENAME = "pyrnnoise-0.4.5-py3-none-win_amd64.whl"
SHA256 = "f46015e5a58212d2883bf8e5a2cafb3c6b115176d8039f8340132cb24866b7da"
DLL_ENTRY = "pyrnnoise-0.4.5.data/purelib/pyrnnoise/rnnoise.dll"
LICENSE_ENTRY = "pyrnnoise-0.4.5.dist-info/licenses/LICENSE"


def install(root, wheel):
    if hashlib.sha256(wheel).hexdigest() != SHA256:
        raise ValueError("RNNoise download failed its integrity check; nothing was installed.")
    destination = Path(root) / "native"
    with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
        files = {"rnnoise.dll": archive.read(DLL_ENTRY), "PyRNNoise-LICENSE.txt": archive.read(LICENSE_ENTRY)}
    destination.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("wb", dir=destination, delete=False) as output:
                temporary = output.name
                output.write(data)
            os.replace(temporary, destination / name)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)


def main():
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        raise RuntimeError("The optional installer requires Windows x64.")
    print("Downloading the pinned RNNoise native library (about 13 MB). No microphone access.", flush=True)
    with urllib.request.urlopen("https://pypi.org/pypi/pyrnnoise/0.4.5/json", timeout=20) as response:
        data = json.loads(response.read(1_000_000))
    entry = next(item for item in data["urls"] if item["filename"] == FILENAME)
    url = urllib.parse.urlsplit(entry["url"])
    if url.scheme != "https" or url.hostname != "files.pythonhosted.org" or entry["digests"]["sha256"] != SHA256:
        raise ValueError("Unexpected RNNoise download metadata.")
    with urllib.request.urlopen(entry["url"], timeout=30) as response:
        wheel = response.read(20_000_001)
    if len(wheel) > 20_000_000:
        raise ValueError("RNNoise download exceeded its size limit.")
    install(Path(__file__).resolve().parent, wheel)
    print("Installed. In Settings, enable Reduce microphone noise and use Test 5 sec before saving.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Noise setup failed ({type(exc).__name__}). Close the HUD and retry; keep suppression off meanwhile.", file=sys.stderr)
        raise SystemExit(1)
