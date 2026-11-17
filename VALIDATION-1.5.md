# Validation record — Meeting HUD 1.5.0

Prepared 17 November 2026. This records the restored, packaged source release, not a deployment to the user's laptop.

## Completed

- Full offline suite: **167 tests passed**. This includes 142 existing tests, with expectations updated for the approved UI/SSE behavior, and 25 added regression tests. Final full run: 6.229 seconds in this development environment; this duration is not a Gemini speed benchmark.
- New tests cover incremental first English before request completion, excluding thought parts, stalled-body cancellation, stalled-header first-output deadline, total timeout after partial output, truncated/missing-final responses, split UTF-8 SSE events, oversized responses, bounded 429 retry respecting Retry-After, no network on pre-cancel, and model-specific thinking configuration.
- Audio/worker cases cover 100 ms PCM frames with a retained tail, explicit backlog failure, phrase order/debounce, draft English while still recording, final source arriving after `turnComplete`, epoch cancellation, setup failure releasing capture, microphone opening only after recognition readiness, no duplicate batch job, and a real QThread accepting a new translation after a stalled request is cancelled.
- Qt tests cover equal caption font and geometry for interim/final output, stable geometry on hover, optional source review, programmatic source updates preserving English, persistent errors, draft/final Copy state and saved appearance fields.
- Summary tests cover quantiles, failure counts and duplicate input handling.
- Every Python/PYW source file parsed with Python 3.11 grammar. `main.py --version` returns **Meeting HUD 1.5.0**.
- Inspected actual offscreen Qt renders of the live HUD, English HUD at minimum width, and Settings. The screenshots were local development checks, not Windows screenshots. The runtime display is implemented in native Qt, not from a generated concept image.
- Original uploaded ZIP retained unchanged; SHA-256: `17128666c2df376887ee0ece19758cf30c8a6c0081b3e3ff235bd91d5beb61be`.
- Source package excludes virtual environments, generated caches, credentials, environment files, audio, transcripts and development logs. Per-file release checksums are supplied for update verification; they are integrity checks, not a digital signature.

## Development environment

| Component | Used for checks |
| --- | --- |
| OS | Linux; Qt offscreen platform |
| Python | 3.12.14, with explicit Python 3.11 syntax parsing |
| PyQt6 | 6.9.1 |
| Qt runtime | 6.9.2 |
| NumPy | 2.3.5 |
| SciPy | 1.17.0 |
| HTTPX | 0.28.1 |
| websockets | 16.0 |

The original Windows dependency lock is unchanged. Its versions differ from this host. Provider tests use mocked HTTP/WebSocket transports and capture tests use mocked PortAudio devices. No user's real API credentials were used.

## Still required on the laptop

- Execute/review the PowerShell updater and confirm backup/rollback behavior. PowerShell was unavailable on this development host; the script was reviewed statically, not executed here.
- Native Windows startup, WASAPI microphone/loopback behavior, optional RNNoise/CUDA compatibility, saved DPAPI credentials, shortcut/supervisor restart and shutdown.
- Capture exclusion, global hotkey/tray behavior and actual display scaling at 100%, 125%, 150%, 200%.
- Gemini account/model availability, Hindi/Hinglish recognition and English quality, real time to first English, completion latency, actual failures and rate limits.
- Final recognition after Stop uses a bounded reception-time/quiet-period heuristic because input transcripts are independent of turn-completion messages. Validate last-word preservation and delayed finals with real audio. Treat incomplete finalization as an error, not a successful translation.

The release is ready for the user's staged installation and test workflow. No sub-second latency or perfect-accuracy result is claimed. Follow the two Antigravity prompts in order.
