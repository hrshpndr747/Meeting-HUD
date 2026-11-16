# Source and third-party notices

Meeting HUD application source in this archive is offered under GPL-3.0-or-later; the complete GPL version 3 text is in LICENSE. This is a source distribution; dependency wheels, models and native runtimes are installed/downloaded separately and retain their own licenses.

- PyQt6: GPL v3 or Riverbank commercial terms. This distribution uses the GPL route. See https://www.riverbankcomputing.com/software/pyqt/intro . Qt has its own licensing terms.
- faster-whisper and OpenAI Whisper: MIT-licensed projects; model distributions retain their published model-card/license terms. See https://github.com/SYSTRAN/faster-whisper and https://github.com/openai/whisper .
- CTranslate2: MIT; NumPy/SciPy: BSD-style licenses; ONNX Runtime: MIT; HTTPX: BSD-3-Clause. Consult each installed distribution's license metadata.
- PyAudioWPatch builds on PyAudio/PortAudio and their respective licenses. See https://github.com/s0d3s/PyAudioWPatch .
- Ollama: MIT project, installed separately. The default Qwen3 4B Instruct model has its separately published Apache-2.0 model license. Other user-selected models may have different terms; an open API is not evidence that model weights are open source.
- Windows and NVIDIA drivers/runtime packages are not claimed to be open source. They are separately supplied under their vendors' terms. Optional Gemini/Groq services have provider terms, quotas and pricing.

The user-supplied Xexamai project (Artasov, MPL-2.0, separate branding notices) was reviewed as inspiration for provider/model configuration. No Xexamai code, logo, name or assets are redistributed in Meeting HUD. Original Python implementations were written for this HUD. Reference: https://github.com/Artasov/xexamai .

No API credentials, downloaded model weights, personal logs, cache files or machine-specific shortcuts are included in this source archive.

## Optional microphone noise processing

The optional installer downloads the published `pyrnnoise==0.4.5` Windows x64 wheel, verifies a pinned SHA-256 digest, and extracts its RNNoise native DLL and PyRNNoise Apache-2.0 license. This project does not copy the Python wrapper implementation. The RNNoise upstream BSD-style license is included in `RNNOISE_LICENSE.txt`; retain that file and the extracted `native/PyRNNoise-LICENSE.txt` with the native library. See https://github.com/xiph/rnnoise and https://github.com/pengzhendong/pyrnnoise.

`websockets` is used for Gemini continuous ASR under its BSD license; https://github.com/python-websockets/websockets.
