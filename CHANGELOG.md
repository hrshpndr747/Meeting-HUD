# 1.5.0 — 17 November 2026

- Text-focused native HUD: shared 15 pt caption font, stable interim/final geometry, hover/keyboard controls and optional source drawer.
- Gemini streaming text output with cancellation, bounded first-output/overall deadlines, one bounded transient retry, safe model-specific thinking controls and persistent incomplete-result errors.
- Explicit microphone streaming for Gemini recognition plus Gemini English translation; stable phrase updates while recording, ordered final phrases, epoch/revision handling and audio-tail preservation on Stop.
- Saved streaming/appearance options, UI-only smoke mode, paused launcher mode, opt-in timing logs and a summary utility.
- Backup-based Windows source updater, separate Antigravity installation/testing prompts, staged test guide and validation record.
- Existing local/Groq paths, live-caption routing, credentials and capture-protection approach retained.

# Changes

## 1.4.0 — Gemini speech, microphone quality and launcher recovery

- Adds actual Gemini inline-audio dictation and source-separated continuous WebSocket captions, including interim text, setup handshake, bounded queues and reconnect notices. Independent speech/text selections and free local defaults remain.
- Adds stable microphone selection, an explicit local five-second meter/clipping check, shared microphone ownership, glossary editing and optional RNNoise at 48 kHz before 16 kHz ASR. Native filtering is off by default and installed separately from a pinned hash-checked wheel.
- Fixes eager Path.home evaluation when APPDATA exists; settings never fall back to shared temporary storage.
- Replaces duplicate-instance modal errors with desktop-scoped restore IPC. The supervisor retains handles through code-75 restarts; the child verifies capture protection before restore.
- Reads bounded provider error bodies, returns safe actionable guidance, and never displays/logs raw bodies that could echo speech or credentials. Groq audio reads are also bounded.
- Adds an explicit selected-model preparation command, detailed setup/acceptance checks and new regression coverage.
- Reviews the Anti-Gravity report without copying its blanket Gemini-generation ban, vanilla-PyAudio substitution, shared-temp credential fallback or truncation-before-redaction bug.

## 1.3.0 — independent models and manual translation

- Retains two HUD modes only: Live Transcript and Translation. Adds a protected, inline settings panel rather than a new workflow.
- Independently routes live speech, manual dictation speech and English text translation. Includes local large-v2 selection, local/Groq ASR and local Ollama/Gemini/Groq text translation.
- Fetches account model catalogs asynchronously, paginates Gemini results, filters unsupported categories and ignores stale replies. Lists installed local Ollama tags; validates selected text/remote capabilities before generation.
- Keeps free local defaults. A saved key does not select cloud; generation requires explicit cloud opt-in. No provider or cloud fallback is automatic.
- Encrypts API keys with Windows DPAPI; preferences contain no secrets. Validates before saving and rolls back encrypted key/preferences bytes on a save failure. Environment overrides are reported.
- Replaces automatic translation segmentation with Record → Stop → recognize the complete utterance → translate its source text. Displays editable recognized text and supports retranslation, cancel, level meter and recording duration.
- Preserves final resampler samples. Fixes a completion-order race that could leave processing stuck, retries failed backend initialization cleanly, and releases local ASR before Ollama uses the GPU.
- Adds minimize/tray restoration and Ctrl+Shift+H recovery. Cancels active recording when hidden. Keeps settings/model lists in the protected HWND and clamps expanded layouts to the screen.
- Adds a supervised pythonw launcher, regenerated shortcut, quiet VBS entry point, duplicate-instance detection and startup/native logs. Preserves the supplied nativeEvent and ONNX-before-Qt workarounds.
- Extends diagnostics for selected model stages and local Ollama metadata. Adds provider/security, manual recording and independent reviewer regression tests.

The uploaded branch's plaintext keys, cloud auto-selection, static speculative Gemini IDs, unprotected settings dialog/popups and absolute `.lnk` were not carried forward. Xexamai supplied design inspiration for independent model/provider selection; none of its interview/exam features, branding or implementation source was copied.

## Prior 1.1 baseline

Pinned Windows GPU runtime packages, typed display-affinity guard, isolated diagnostics, bounded live queues, WASAPI reconnects, Silero filtering and fixed glossary support are retained.
