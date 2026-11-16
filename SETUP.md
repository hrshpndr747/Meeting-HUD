# Windows setup and model selection

Target: Windows 11 x64, Ryzen 7 7840HS, 16 GB RAM, RTX 3050 Laptop GPU with 6 GB VRAM. Windows 10 build 19041+ is the API minimum. This release was developed/tested on Linux with simulated native devices; perform TEST_GUIDE.md on the actual laptop.

## 1. Clean upgrade

Close the older HUD first. Extract this ZIP into a fresh writable folder, such as a folder under your user profile, and open the enclosed `meeting_hud` directory. Do not run inside the ZIP or copy an old `.lnk`: old shortcuts contain absolute machine paths. This release generates its own shortcut.

The uploaded branch stored settings beside the code and could put API keys into plaintext `settings.json`. This release deliberately does not import that file or automatically select cloud because a key exists. Re-enter the desired model choices and keys through Settings. Non-secret preferences now live at `%APPDATA%\MeetingHUD\settings.json`; keys are separate Windows-DPAPI-encrypted files in that directory. Old project settings are not included in the new archive. Remove old plaintext keys from your own old copies when no longer needed.

You may copy a complete old `models` directory to the new project to reuse cached Whisper weights. A cache containing only a `refs` file is incomplete: the actual model blobs are also needed. Never copy `.venv` between folders/machines; create it again.

## 2. Prerequisites

- Install 64-bit CPython 3.11 including the `py` launcher.
- Install/repair the x64 [Microsoft Visual C++ Redistributable](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist).
- For CUDA, install an [NVIDIA driver](https://www.nvidia.com/Download/index.aspx) compatible with CUDA 12.8. Check `nvidia-smi`; the integrated Radeon GPU does not provide CUDA.
- Allow desktop microphone access in Windows privacy settings. Select the intended default microphone and output device in Windows and use the same output in your meeting app.
- Use a headset to reduce duplicate capture of speakers through the microphone. No virtual audio cables are required.

Run `setup.bat` once. It creates the venv, installs the pinned packages, runs `pip check`, and creates `Meeting HUD.lnk`. Setup intentionally shows a console so installation errors are visible. Large downloads are expected: the three NVIDIA runtime wheels alone are about 1.33 GB compressed, plus speech and language models. Allow at least 12 GB free disk space for the recommended setup and caches.

Equivalent PowerShell installation, from the project folder:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.txt -r requirements-gpu.txt
.\.venv\Scripts\python.exe -m pip check
```

The GPU pins are cuBLAS `12.8.5.5`, cuDNN `9.10.2.21`, and NVRTC `12.8.93`. No CUDA compiler/toolkit or PyTorch is required by the application. PyTorch's optional file is only for separate diagnostics. CPU-only users can omit `requirements-gpu.txt` and select CPU in Settings; inference can be substantially slower.

The dependency metadata and Windows CPython 3.11 wheel availability were checked during the earlier dependency audit. A full Windows installation is still an acceptance test, not something proved by the Linux unit tests.

## 3. Completely local, no API payment

Install [Ollama for Windows](https://ollama.com/download/windows). Run:

```powershell
ollama pull qwen3:4b-instruct
```

Keep Ollama running locally on its default `127.0.0.1:11434` endpoint. Do not select a cloud model or a remote alias. The app checks `/api/show` before each translation and rejects remote models and models without text-generation capability. It never follows HTTP redirects or environment proxies for local processing.

Keep these Settings choices for the starting profile:

| Stage | Provider | Model |
| --- | --- | --- |
| Live Transcript | Local Whisper | `small` |
| Recognize my speech | Local Whisper | `large-v3` |
| English translation | Local Ollama | `qwen3:4b-instruct` |

Whisper weights download to `meeting_hud/models` on first use. Ollama owns its own model cache. After both are cached, set `HUD_OFFLINE=1` to prevent Whisper downloads and verify the local route with the network disconnected. Ollama must remain running. No API key or subscription is needed for this profile; hardware, electricity and download costs remain your own.

For stronger live recognition, select `large-v2` or `large-v3`; compare latency and dropped chunks on the same recording. `medium` is another speed/accuracy tradeoff. `turbo` is allowed for ASR because English translation now happens as a separate text step. Local recognition defaults to GPU and falls back to CPU if CUDA fails. Hindi dictation uses language `hi`; choose `auto` for broader multilingual input, or a known code such as `ta`. Short audio can make automatic language identification unreliable.

For the 6 GB GPU, Whisper is released before local Ollama translation and Ollama is requested to unload afterward. Close unrelated GPU-heavy programs for a fair test. Repeated loading increases time-to-result; this is an accuracy-oriented turn-based translation flow, not simultaneous word-by-word output.

## 4. Your local + Gemini combination

1. Click the gear icon.
2. Under **Live Transcript**, choose Local Whisper → `large-v2` (or keep `small` for speed).
3. Under **Recognize my speech**, choose Local Whisper → `large-v3`. This recognizes your microphone audio locally.
4. Under **English translation**, choose Gemini API. Paste your Gemini API key into the masked field and click **Load models**.
5. Select a returned text-capable Gemini model. Use the account model list to check current availability; no unavailable model is silently replaced.
6. Check **Allow selected cloud services**, then **Save and restart**.

This route sends the recognized/edited text to Gemini, not your audio. An API key alone does not activate cloud processing. Discovery is a metadata request, not a generation test; returned models can still fail because of region, access, quota or account restrictions. The app cannot determine or enforce your provider billing tier. For zero API charges, keep all providers local. A provider's free quota is not a promise of unlimited usage.

Groq is also available independently: speech stages list Whisper models; the English stage lists text candidates. Groq speech uploads only the selected stage's audio plus the optional glossary. No provider is used as a fallback for another. Cloud 401/403/404 errors ask you to check the key/model; rate-limit and server errors have one bounded retry. Failed translations preserve recognized text for a later retry.

Keys are encrypted with Windows DPAPI for the current Windows account. They are never written to preferences or diagnostic reports. “Forget saved key” takes effect after Save and restart. Environment keys override saved keys and must be removed from the environment to forget them. DPAPI protects storage at rest; it does not protect against processes already running with access to your Windows account.

### Gemini speech recognition and continuous captions

Under **Recognize my speech**, select **Gemini API**, paste a key, and Load models. Choose `gemini-3.5-transcribe` if available to your account, or a returned audio-capable Flash/Pro model. The Translation mode still records only after Record and recognizes the whole recording after Stop. Gemini Transcribe requests VERBATIM recognition, Hindi/Indian-English hints, optional vocabulary, inline WAV and `store:false`. This disables interaction retrieval storage; it does not override provider data-handling terms. Flash/Pro receives a literal transcription instruction and the WAV. No Files API upload is created.

Under **Live Transcript**, Gemini uses a dedicated `transcribe-live` model (`gemini-3.5-transcribe-live` is the current suggested ID). The app sends PCM16 mono 16 kHz in 100 ms frames after the WebSocket setup acknowledgement. Mic and meeting output have independent connections. Interim captions update in place; only final input transcriptions enter history. Model-generated replies/audio are never played or used as captions.

This cloud live mode sends continuous audio, including pauses, and opens two sessions. Check provider quota/pricing before selecting it. Session renewal, network loss and backlog may create caption gaps; notices report these instead of replaying old meeting audio. Pause, Settings, mode switch and shutdown cancel obsolete streams. Existing in-flight network requests may take their bounded timeout to finish. No silent cloud/model switch occurs.

Model metadata and access are not identical. An account may list a model but fail to generate; quota or project restrictions can also differ. Gemini 2.5 IDs are not blanket-blocked solely because another account could not use them. 400/403/404/429 errors have actionable guidance with raw bodies and credentials omitted.

### Microphone selection, Hindi/Hinglish and noise

1. Open Settings → Recognize my speech (or Live Transcript). Click **Select mic**, choose your headset, then **Test 5 sec**. Speak normally; the level meter stays local and no test audio goes to a model.
2. If quiet, move the mic nearer or adjust Windows input level. If clipping, lower gain. Keep the meeting on headphones; RNNoise is not acoustic echo cancellation.
3. Local Hindi ASR uses `hi`; compare `auto` on your mixed-language speech. Gemini dedicated ASR uses Hindi plus Indian English hints for `hi`, automatic detection for other language codes. Flash/Pro dictation is instructed to preserve Hindi and English words.
4. Add a short comma-separated glossary of your names/technical terms. Vocabulary is a hint, not a replacement for clear speech.
5. Optional: close the app, run **setup_noise.bat**, relaunch, enable **Reduce microphone noise**, and repeat the test. This downloads a hash-verified PyRNNoise 0.4.5 Windows wheel and extracts only its native RNNoise DLL/license; unrelated Python plotting/FFmpeg packages are not installed. The DLL and models are not shipped in this source ZIP.
6. Compare noise suppression off/on with the same phrases, then Save and restart. Suppression affects the mic only, runs at 48 kHz before ASR resampling to 16 kHz, and preserves the Stop tail. It may harm very quiet speech; leave it off if recognition worsens.

The saved microphone name is resolved against fresh WASAPI enumeration, never a stale numeric device index. If names are duplicated, rename one in Windows or follow the Windows default. A missing selected mic is reported; the app does not quietly record another device.

### Prepare the selected local models

After choosing and saving your models, run **prepare_models.bat** before the first meeting. It downloads only the selected local Whisper models and pulls the selected Ollama tag. Keep Ollama running. It shows download progress in a console and performs no cloud speech/translation request. Downloads may be several GB and are intentionally separate from normal quiet launch.

## 5. Run without CMD

Use **Meeting HUD.lnk** created by setup. Alternatively use `Launch_HUD.vbs` where Windows Script Host is enabled. These launch `.venv\Scripts\pythonw.exe` through the supervisor, keep no CMD open, prevent duplicate instances of the same project on the same Windows desktop and restart after saved settings.

`run.bat` is a compatibility launcher; Windows may briefly flash a console when a batch file is double-clicked. Use the shortcut or VBS for zero-console launching. `run_debug.bat` intentionally keeps diagnostic output visible on failure. When starting `main.py` directly, settings save exits with code 75; launch it again to apply settings. The supervisor handles that restart automatically.

Double-clicking the shortcut while the HUD is running sends a restore request instead of opening a second HUD or showing a blocking “already running” message. The supervisor keeps that event alive across startup and Settings restart. The HUD verifies capture protection before becoming visible. Foreground focus is best effort.

Startup fixes include returning `(False, 0)` for unhandled native events (instead of the SIP base call implicated in your report), and preloading ONNX Runtime before Qt. These preserve the workarounds from the supplied Windows report; the exact cause of every earlier crash is not independently proven here.

Logs go into `meeting_hud/logs`: `startup.log`, `console.log`, `native.log`, and the supervisor's `launcher.log`. The app does not log transcript/audio/key values. Native crash traces and model-download failures may contain local paths. Rotate/review logs before sharing. A startup failure produces a launcher message with the log folder instead of silently disappearing.

## 6. Diagnostics and operation

```powershell
.\.venv\Scripts\python.exe main.py --version
.\.venv\Scripts\python.exe main.py --diagnose --report diagnostics.json
.\.venv\Scripts\python.exe main.py --diagnose --check-model --report diagnostics.json
```

Ordinary diagnostics enumerate devices, inspect native packages and test Silero with generated silence. The local Ollama probe checks model metadata without sending speech/text. `--check-model` additionally loads/warms the selected local live and dictation models sequentially and may download them. Inspect `model.stages.live` and `model.stages.dictation`; a 300-second probe timeout can expire on the first large download. No paid generation calls are made by diagnostics. “Report written” does not mean every probe passed.

During Translation, Record begins capture; Stop → Translate closes capture, preserves the final audio tail and starts recognition/translation. Pauses never end the recording automatically. At 60 seconds it stops and submits the utterance as a safety bound. Cancel invalidates the job; already-running native inference or HTTP requests finish cooperatively, so a new recording may wait until they return. Closing immediately hides the HUD but waits for those operations; native model/download operations are not forcibly terminated.

Minimize and Ctrl+Shift+H hide locally. Restore with Ctrl+Shift+H or the tray icon. Hiding an active recording cancels it; a stopped utterance already processing can finish. Live captions continue while hidden. Opening Settings pauses capture; closing it restores the preceding pause state.

## 7. Advanced environment settings

UI settings persist across restarts. Explicit environment variables take precedence; the UI reports a conflict instead of pretending a changed value will apply. Variables below affect the current shell and its child processes when set with PowerShell `$env:NAME = 'value'`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `HUD_BACKEND`, `HUD_MODEL` | `local`, `small` | Live recognition provider/model |
| `HUD_DICTATION_BACKEND`, `HUD_DICTATION_MODEL` | `local`, `large-v3` | Manual speech recognition provider/model |
| `HUD_TEXT_TRANSLATOR` | `ollama` | `ollama`, `gemini` or `groq` |
| `HUD_OLLAMA_MODEL` | `qwen3:4b-instruct` | Explicit local model:tag |
| `HUD_GEMINI_MODEL` | empty | Exact returned Gemini model ID |
| `HUD_TEXT_MODEL` | `llama-3.3-70b-versatile` | Groq text model; use discovery to check availability |
| `HUD_CLOUD_ENABLED` | `0` | Must be `1` for cloud speech/text generation |
| `GEMINI_API_KEY`, `GROQ_API_KEY` | unset | Optional environment credentials; never paste keys into source files |
| `HUD_DEVICE`, `HUD_CPU_THREADS` | `auto`, `6` | Compute choice and CPU thread count |
| `HUD_TRANSLATION_LANGUAGE`, `HUD_TRANSCRIPT_LANGUAGE` | `hi`, `auto` | Recognition languages |
| `HUD_INITIAL_PROMPT` | empty | Fixed glossary of names/terms, at most 200 characters |
| `HUD_MICROPHONE` | empty | Stable `wasapi:` name; empty follows Windows default |
| `HUD_NOISE_SUPPRESSION` | `0` | `1` enables the optional RNNoise mic filter |
| `HUD_MAX_RECORD_SECONDS` | `60` | Manual recording cap, 5–120 seconds |
| `HUD_MIN_CHUNK_MS`, `HUD_MAX_CHUNK_MS`, `HUD_PAUSE_MS` | `800`, `3000`, `240` | Live caption segmentation only |
| `HUD_ENERGY_FLOOR` | `0.003` | Capture threshold; lower slightly for quiet microphones |
| `HUD_MODEL_CACHE` | project `models` folder | Whisper download/cache directory |
| `HUD_OFFLINE` | `0` | `1` requires already-cached Whisper weights |
| `HUD_CUDA_BIN` | unset | Optional absolute DLL directories, separated by semicolons |

Use a glossary like `Project Lotus, Rohan, Kubernetes, invoice reconciliation.` It biases recognition toward vocabulary but does not guarantee exact names. No old transcript is accumulated into the glossary.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| App immediately exits | Use the newly generated shortcut; open `logs`, then run `run_debug.bat` |
| ONNX/Qt startup error | Repair pinned packages and x64 VC++ runtime; do not move Qt imports before bootstrap |
| Missing CUDA DLL / loader error 126 | Install GPU requirements in the same venv; remove stale `HUD_CUDA_BIN`; inspect diagnostics |
| CPU on the RTX laptop | Check driver, available VRAM, diagnostic fallback reason and selected device |
| No meeting audio | Match the meeting output to the Windows default output; inspect WASAPI loopback in diagnostics |
| Translation has no speech | Press Record; use Select mic / Test 5 sec; check permissions, mute, waveform and input volume |
| Ollama unavailable/model missing | Start Ollama and pull the exact configured tag; click Translate text to retry |
| Model list empty/HTTP error | Check provider key/access/quota; model discovery does not prove generation entitlement |
| Save blocked by environment | Remove the named environment override or edit that variable before restarting |
| Double meeting text | Use a headset; acoustic echo cancellation is not implemented |
| Display-affinity/hotkey failure | Fix the reported Windows/hotkey issue; no unprotected display bypass is offered |

Primary references: [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [CTranslate2](https://opennmt.net/CTranslate2/installation.html), [PyAudioWPatch](https://github.com/s0d3s/PyAudioWPatch), [Gemini models API](https://ai.google.dev/api/models), [Gemini billing](https://ai.google.dev/gemini-api/docs/billing), [Groq models](https://console.groq.com/docs/models), [Ollama API](https://docs.ollama.com/api), [Windows display affinity](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowdisplayaffinity).

Gemini speech references: [audio transcription](https://ai.google.dev/gemini-api/docs/transcribe), [live transcription](https://ai.google.dev/gemini-api/docs/live-api/live-transcribe), [Interactions API](https://ai.google.dev/api/interactions-api). Noise: [RNNoise](https://github.com/xiph/rnnoise), [pinned PyRNNoise wheel](https://pypi.org/project/pyrnnoise/0.4.5/).
