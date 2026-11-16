# Validation record — Meeting HUD 1.4.0

Date: 25 October 2026. Status: **tested release candidate; Windows laptop acceptance pending**.

| Check | Observed result |
| --- | --- |
| Portable suite | **142 tests passed** in 3.507 seconds; CPython 3.12.14 / Linux |
| Compilation | All 43 application, launcher and test Python source files compile |
| Launcher review | Independent reviewer implemented/reviewed IPC and supplied 12 passing focused tests |
| Streaming review | Independent reviewer found an obsolete-error epoch race; corrected with an async regression |
| Qt visual inspection | Live/partial captions, Translation and Gemini speech settings rendered offscreen; 520×140 live, 520×260 translation, protected scrollable settings |
| Native noise smoke test | Actual Linux RNNoise binary processed synthetic 16 kHz, 44.1 kHz and 48 kHz inputs; finite bounded output, retained duration/tail, state released |
| Windows noise download | Published Windows wheel SHA-256 verified against the installer pin; DLL not executed on Windows here |
| Dependency audit | 41 core/optional-GPU packages have compatible pinned metadata and Windows CPython 3.11 x64 wheels; no audit errors |
| CLI | --version returns Meeting HUD 1.4.0; diagnostics writes valid JSON |
| Provider calls | HTTPX/WebSocket fakes only; no real API credentials, audio uploads or paid generation calls |
| Distribution | Source, tests, guides, license and setup/launch scripts only; no keys, settings, logs, weights, native DLL, venv, machine shortcut or reference-project code |

## Coverage

The suite covers rational resampling, flush tails, VAD boundaries, full-utterance recording, silent frames, cancellation, stale jobs/results/errors, mode transitions, bounded queues, simulated device reconnects, real Qt worker delivery and mic ownership cleanup. Selected microphones survive index reordering; duplicate/missing names fail explicitly. A cancelled mic test releases its stream and lease.

Gemini tests assert inline mono16k PCM16 WAV, Hindi/English hints, vocabulary, correct Interactions verbatim object and store:false; Flash/Pro transcription input and thought exclusion; compatible model catalogs without a blanket-generation ban; explicit cloud opt-in; bounded/malformed errors and secret-safe guidance. Streaming tests cover setup acknowledgement before audio, PCM encoding, separate sources, interim replacement, final input after turnComplete, ignored model output, cancellation, backpressure, goAway and stale-prefix recovery.

IPC tests simulate native handles and object lifetime: event-before-mutex election, queued restore across restart, orphaned-child presence, desktop/install separation, debug no-op, access errors, typed 64-bit handles, duplicate-launch silence and supervisor exit-75 restarts. Capture-affinity/DPAPI tests are simulations, not native Windows proof.

## Environment limits

No Windows desktop, WASAPI device, NVIDIA GPU, speech-model weights, Ollama server or live provider credentials were available. Diagnostics therefore records Windows audio/CUDA as skipped, capture exclusion as not tested, missing speech dependencies as an error and Ollama as unavailable. These are not passing hardware checks.

RNNoise testing used synthetic signals on Linux; it does not establish Hindi recognition improvement, listening quality or Windows DLL compatibility. Offscreen rendering also cannot validate Windows scaling, fonts, taskbar behavior or capture exclusion.

Still required on the laptop: fresh Python3.11 setup, GPU warmup, microphone/noise A/B, real Hindi/Hinglish source accuracy and translation fidelity, latency/memory, device changes, repeated quiet launches and restores, DPAPI, provider access/quota, a long cloud session, and Snipping Tool/Teams/Zoom/Meet/OBS capture tests including Settings and restored windows. See TEST_GUIDE.md.

The independent reviewer completed the focused IPC work and one streaming race review; full independent final sign-off remains incomplete. The primary engineer completed the final source review and portable validation. No perfect-score or universal-invisibility claim is made.
