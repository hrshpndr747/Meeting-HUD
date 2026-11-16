# Meeting HUD 1.4.0

A minimal Windows HUD with exactly two working modes: **Live Transcript** and **Translation**. Models are selected independently in the gear/settings panel. The default route runs locally and has no API fee or subscription.

## Start here

1. Extract into a fresh, writable folder. Install Python 3.11 x64 and the x64 Microsoft Visual C++ Redistributable.
2. Run `setup.bat`. It installs the pinned application/GPU packages and creates `Meeting HUD.lnk` for this folder.
3. For completely local translation, install [Ollama](https://ollama.com/download/windows), then run `ollama pull qwen3:4b-instruct` once.
4. Double-click **Meeting HUD.lnk**. `Launch_HUD.vbs` is an alternative where Windows Script Host is enabled. Neither keeps a CMD window open.
5. Open Settings to choose your models. Read [SETUP.md](SETUP.md) for prerequisites, upgrades and troubleshooting.

## Choose models independently

| Stage | Available providers | Default |
| --- | --- | --- |
| Live Transcript | Local Whisper, Groq Whisper or Gemini streaming ASR | Local `small` |
| Recognize my speech | Local Whisper, Groq Whisper or Gemini audio ASR | Local `large-v3` |
| English translation | Local Ollama, Gemini API or Groq API | Ollama `qwen3:4b-instruct` |

Your requested combination is supported: **local `large-v2` for live captions + local Whisper recognition + Gemini for English translation**. In Settings, select English translation → Gemini API, enter a key, click Load models, and choose a returned model. Enable cloud services only if you want those requests. Save and restart applies the choices.

Entering a key alone does not enable cloud processing. Model discovery lists provider-returned candidates; it does not grant model access, guarantee free usage, or change your billing account. There is no automatic cloud fallback. Selecting Gemini/Groq for speech sends audio. Selecting them only for English translation sends recognized text. Cloud quota, pricing and data handling belong to the provider.

## New in 1.4

- Gemini appears under **Live Transcript**, **Recognize my speech**, and **English translation**, each with a compatible model list. Live uses a dedicated `transcribe-live` model; dictation uses Gemini Transcribe or an audio-capable Flash/Pro model. Current defaults are selection suggestions, not guaranteed account access.
- **Select mic** and **Test 5 sec** identify quiet/clipping inputs before recognition. Selection follows a device name across index changes; missing or duplicate names fail explicitly.
- Optional local RNNoise suppression processes only the microphone. Run `setup_noise.bat` once, then enable the checkbox and compare the mic test. It defaults off. No noise filter can reliably recover speech masked by louder speech/noise.
- Settings handles missing Windows home lookup safely. A second shortcut launch restores the current HUD; a restore requested during startup or Settings restart remains queued. Separate Windows desktops have separate instance identities.
- `prepare_models.bat` downloads only your selected local models before a meeting. It does not bundle a virtual environment or model weights into the ZIP.

## Use the two modes

- **Live Transcript:** captures the default Windows WASAPI speaker loopback and your selected microphone. Completed phrases appear continuously, labelled MEETING or YOU. Gemini streaming also shows an interim caption that is replaced as recognition improves. Pause closes capture streams. Audio is not archived.
- **Translation:** microphone is off until you click **Record**. Speak through pauses, then click **Stop → Translate**. The complete utterance is recognized first; those words appear in an editable box; English follows in cyan. Correct a word and click **Translate text** to retry without recording again. Cancel discards pending results. A recording has a 60-second safety limit by default.

Copy copies the latest English sentence. Minimize hides the HUD to its tray icon; click the icon, double-click the application shortcut again, or press **Ctrl+Shift+H** to restore. Hiding during a recording cancels that recording. Hiding Live Transcript leaves live capture running. Opening Settings pauses capture and invalidates pending work. The close icon stops the app cooperatively.

The live HUD defaults to 520 × 140 logical pixels. Translation expands to fit source and English. Settings stays inside the same protected window and returns to the previous size when closed. Drag the background to move; use the bottom-right resize grip.

## Quality and practical limits

Whisper `large-v3` with a complete utterance and a separate text translator is the accuracy-oriented local route. `large-v2` is also available. Start with `small` for live captions if latency matters: a larger model on the RTX 3050 may drop more old live chunks when it falls behind. All-local dictation releases Whisper before Ollama and unloads Ollama after its response to avoid intentionally holding both models on the 6 GB GPU. This introduces model-loading delay between recordings. Actual quality and speed need measurement on your speech; ChatGPT/Gemini parity is not promised.

Local/Groq live captions use 0.8–3.0 second chunks and bounded queues. Gemini live ASR streams 100 ms PCM frames over separate mic/system connections, with interim and final captions. Interrupted/expired connections reconnect without replaying stale audio; notices disclose possible gaps. This is not a complete meeting archive. Local CPU fallback is automatic and visibly reported. No echo cancellation, speaker diarization, answer generation, interview features or meeting recording is included.

## Capture exclusion

The main HWND receives `WDA_EXCLUDEFROMCAPTURE` (`0x11`) before first display, with return-value/readback verification, DWM checks and periodic rechecking. A failed check hides the HUD. Settings, model lists and masked key fields are child widgets in that window; there are no separate settings dialogs, tooltips or dropdown popups.

**This is not a universal 100% invisibility guarantee.** Microsoft describes display affinity as protection for supported OS capture paths, not a security boundary. Test Snipping Tool and each actual Teams/Zoom/Meet/OBS capture mode on your machine. See [TEST_GUIDE.md](TEST_GUIDE.md).

## Contents and validation

- [SETUP.md](SETUP.md): complete Windows setup, model/API settings, offline use and startup recovery.
- [REVIEW.md](REVIEW.md): Builder/Critic iterations, defects found and remaining evidence gaps.
- [VALIDATION.md](VALIDATION.md): automated results and target-machine tests not performed here.
- [TEST_GUIDE.md](TEST_GUIDE.md): repeatable acceptance tests, including accuracy and capture exclusion.
- [CHANGELOG.md](CHANGELOG.md): changes from the uploaded branch.
- `tests/`: portable regression tests using real Qt worker threads, simulated devices/models and mocked provider responses.

The application source is GPL-3.0-or-later; see [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). The Windows/NVIDIA stack and optional provider services have their own terms. Original application code is provided in full; models and external runtimes are downloaded separately.
