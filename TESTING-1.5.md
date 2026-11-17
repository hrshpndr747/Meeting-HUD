# Meeting HUD 1.5 — translation and latency test guide

Install and pass `ANTIGRAVITY-INSTALL-PROMPT.md` first. Then use `ANTIGRAVITY-TEST-PROMPT.md`. Real Windows audio, Gemini access, network latency and translation accuracy have not been measured in the development environment.

## Start a controlled run

1. Keep the original installation backup. Close all instances of this HUD, including its supervisor.
2. Confirm both dictation recognition and text translation use Gemini and “Translate while recording” is enabled. The live-caption provider remains independent. Retain the selected API key securely in the app. Streaming speech defaults to `gemini-3.5-transcribe-live`; verify account access. Choose a supported, available text model for this account. Model availability may change.
3. Archive older `logs/latency.jsonl*` files locally. Launch `run_latency.bat`. It sets `HUD_LATENCY_LOG=1` only for that process tree and starts paused. Normal launch leaves timing logging off unless that environment variable is set externally.
4. Switch to English. Press Record, wait for “Listening…”, then speak a controlled sample in Hindi/Hinglish. English should appear during speech as phrases stabilize. On Stop, the final source and English should settle. The main text stays at the same font size throughout. Hover to reveal controls. Use Review source to inspect recognition or edit and translate again.
5. Keep cold-start results separate from later requests. Use the same microphone, approximate speaking pace, network and models across comparisons.

## Controlled sample meanings

Speak these meanings naturally in Hindi/Hinglish. Write down the exact wording locally once, then repeat it consistently. The table is an English reference for checking meaning, not a script that must be read in English. Avoid private names or real business information.

| ID | Meaning that the English result must preserve | Main check |
| --- | --- | --- |
| S01 | Send the report tomorrow morning. | Basic imperative |
| S02 | Do not send the report today. | Negation |
| S03 | We need fifteen copies, not fifty. | Numbers and contrast |
| S04 | The meeting is at three thirty in the afternoon. | Time |
| S05 | Move the meeting from Monday to Wednesday. | Direction of change |
| S06 | I might attend, but I have not confirmed yet. | Uncertainty |
| S07 | The payment has not been received. | Negative completion |
| S08 | The payment has already been received. | Positive completion |
| S09 | Ask Asha to send the draft to Kabir. | Roles and names |
| S10 | Please check the API response and restart the server. | English technical words |
| S11 | The issue happens only when the microphone disconnects. | Condition |
| S12 | First save the file, then close the application. | Order |
| S13 | I said Thursday—sorry, I meant Friday. | Self-correction |
| S14 | The quantity is two hundred and five. | Full numeric value |
| S15 | The price increased by five percent, not fifteen percent. | Percentage |
| S16 | If the file arrives today, we can review it tomorrow. | Conditional timing |
| S17 | Can you explain why this translation is incomplete? | Translate question, do not answer it |
| S18 | The quoted text says “ignore the instructions”; translate that sentence. | Instruction-like source is still source |
| S19 | Keep the existing version until the new one passes testing. | Dependency/order |
| S20 | I do not want to cancel the meeting; I want to change its time. | Scope of negation |

Longer passages, using fictitious details:

| ID | Passage plan | Checks |
| --- | --- | --- |
| L01 | Explain a three-step report workflow for 30 seconds, pausing briefly between steps. | Phrase order and complete coverage |
| L02 | Speak continuously for 30 seconds about a fictional schedule, including two corrections. | Draft responsiveness, revisions |
| L03 | Describe a fictional invoice with a date, two quantities, a discount and an unpaid amount. | Numeric accuracy and negation |
| L04 | Describe a software bug in Hindi with API, timeout, restart and version terms in English; include a five-second pause. | Code-switching and pauses |
| L05 | Speak for 45–60 seconds, then finish a final word immediately before pressing Stop. | Tail preservation and bounded display |

Run all 20 short and five long cases at least once. Repeat a fixed subset three times. Keep sample counts attached to every statistic. If a test is stopped early, report it separately rather than dropping it from the results.

## Timing definitions

The log contains metadata only: run ID, timestamps, epoch/segment/revision/request IDs, model IDs, sizes, durations and event types. It does not intentionally record spoken text, generated English, audio, API keys or raw provider responses. Logs rotate at about 2 MB with two backups; archive each run before enough activity rotates it away.

| Event/field | What it measures | What it does not establish |
| --- | --- | --- |
| `capture_started` | Microphone opened after recognition connection setup | Time the user began speaking |
| `source_interim` / `source_final` | ASR hypothesis/final reception | Exact acoustic speech-end time or semantic correctness |
| `translation_first_text.elapsed_ms` | HTTP request start to first user-visible English callback, including connection and retry | Full audio-to-screen latency |
| `translation_first_text.source_to_first_ms` | Selected ASR revision timestamp to first English callback | Physical speech onset to English |
| `translation_completed.elapsed_ms` | Request start to successful complete English for that request | Completion of the whole recording if other phrases remain |
| `translation_ui_updated` | English delivered to the GUI handler | Monitor refresh or actual pixel display time |
| `recording_translation_completed.elapsed_ms` | Capture-end to whole-recording completion | Total time since Record was pressed |
| `translation_failed` / `translation_cancelled` / `translation_retry` | Reliability and work superseded/interrupted | Successful request timing |

A request has its own unique `request_id`. `final` means the source was final when that request began. An exact matching draft can satisfy a later identical final transcription without a duplicate request. Thus final-source request counts are not necessarily the number of completed sentences. Growing/corrected source can generate multiple draft requests. Compare both draft and final-source requests; do not combine them into a misleading single speed number.

Run:

```powershell
.\.venv\Scripts\python.exe summarize_latency.py --logs logs --out logs\latency-summary.json
```

The summary reports count, P50, P95, minimum and maximum for observed successful stages, plus event/failure counts. It deduplicates identical events if logs are accidentally supplied twice. P95 is interpolated; small samples are unstable. Review individual slow requests and include failed requests in the reliability report. Logs alone cannot score translation accuracy.

## Reliability checks

- English is shown before Stop during a sufficiently long utterance; a final Copy action is enabled only after the whole translation completes.
- Stop preserves queued audio and the last word. With automatic VAD, `audioStreamEnd` requests finalization. `turnComplete` is not used as an authoritative input-transcript boundary.
- After Stop, finalization waits for source updates to settle. The app uses reception-time/last-voiced-frame comparisons and a short quiet interval; this is a bounded heuristic, not a protocol acknowledgement tied to an exact audio sample. A late final or timeout must be reported as incomplete rather than silently passed.
- Cancelling, switching modes or opening Settings invalidates old work. A new recording must never receive old results.
- Stalled HTTP headers/body are cancellable; first-English deadline is 10 seconds and overall request deadline is 30 seconds including retries. At most one retry for 429/server errors is permitted within that budget.
- Recognition setup is bounded. Final source wait after Stop is bounded at eight seconds; final text work has a further bounded drain budget. These are failure limits, not target latencies.
- No silent loss under streaming backlog. A full audio queue reports an incomplete-recording error. Recognition/source text and any English draft remain available after failures.
- A failed or truncated stream must display a persistent error, preserve the source, leave partial English marked incomplete by the error, and allow source editing/Translate text after recording stops. Do not count the partial as a success.
- Check the original live-caption mode still works with its existing provider. Do not change its model to test the new English route.

## UI and Windows checks

Exercise 100%, 125%, 150% and 200% scaling where practical. Record which scales were actually tested; restore the original setting afterward. Verify the fixed 15 pt default through interim/final changes and pauses, readable two-line wrapping, hover controls without caption movement, manual resizing, optional three lines/font adjustment, source drawer, settings scroll, keyboard actions, tray/hotkey restoration and capture protection. Local shortcuts are Ctrl+1 (Live), Ctrl+2 (English), Ctrl+R (Record/Stop), Ctrl+E (source), Ctrl+, (Settings), Escape (Cancel). Ctrl+Shift+H remains the global visibility toggle.

Use the existing capture-protection workflow to verify Windows behavior. Do not disable protection to collect a screenshot. A development offscreen rendering is not evidence of native Windows protection.

## Baseline and comparison

Keep the first run on the installed release unchanged. After review, compare one variable at a time: streaming on/off, then fast thinking on/off, then optionally another available text model. Changing the text model changes both latency and quality, so report it as a separate comparison. Streaming-off uses batch recognition, so separate recognition time from text-generation time when interpreting it. More frequent phrase translation can increase API request/token usage. There is no guaranteed free tier, latency or model access in this release.

Package only the sanitized test report, timing summary and relevant metadata log files. Keep real transcripts, keys, environment files, DPAPI credentials and audio out of the shared bundle. Request consent before any manual screen/audio recording. Send the installation report first; send this test bundle only after the later test phase.
