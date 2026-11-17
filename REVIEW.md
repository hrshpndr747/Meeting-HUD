> Historical 1.4 documentation retained for reference. For the 1.5.0 update, use README.md, UPGRADE.md, TESTING-1.5.md and VALIDATION-1.5.md. Old UI descriptions and prior test counts below do not describe this release.

# Builder / Critic review — 1.4.0

Scope: retain two HUD modes while adding Gemini audio recognition and live ASR, better microphone controls, optional noise processing, and the supplied Anti-Gravity startup/recovery fixes. Local defaults remain free of API charges.

## Iteration 1 — implementation

**Builder:** added separate Gemini audio-file and continuous Live API backends, stage-specific catalogs, stable WASAPI microphone selection, an explicit local mic test, shared microphone ownership, optional RNNoise processing, model preparation scripts, safe provider errors and lazy settings-folder resolution. An independent reviewer implemented the desktop-scoped launcher/restore coordinator and its focused tests.

**Critical review:** the following defects were found during implementation and review; all have code changes and regression coverage where portable verification is possible.

| Finding | Correction |
| --- | --- |
| Gemini was text-only; speech provider selectors omitted it | Actual WAV/Interactions or Flash/Pro audio recognition and dedicated transcribe-live WebSocket routing; separate model role validation |
| A text or conversational Live model could be confused with a speech model | Filter the live catalog for dedicated transcription plus bidi capability; use dictation/text-specific filters and execution-boundary validation |
| Interactions transcription mode differs from the Live API enum | Use `{ "type": "verbatim" }` for Interactions; retain `VERBATIM` for Live; cross-check official schema and test payloads |
| Audio could be uploaded before setup completed | Wait for a valid setupComplete message before starting the audio sender |
| Interim captions could become repeated history or model replies | Replace interim text in a separate label; commit only final input transcription; ignore output transcription/model audio and turnComplete ordering |
| Slow handshakes could repeatedly reconnect over stale audio | Remove stale prefixes before streaming; report the skipped interval; detect later sequence gaps/backlog and reset the affected connection |
| An obsolete connection's permanent account error could fault the new mode | Independent reviewer found this epoch race; discard obsolete errors before fault/retry decisions. The same protection was applied to ordinary speech jobs |
| Cancelling a mic test after dequeue could be undone when the worker cleared a flag | Generation-based cancellation invalidates even a dequeued request; cleanup releases the microphone lease |
| Numeric microphone indexes can select the wrong device after reordering | Persist WASAPI name identity, re-enumerate, and reject missing/ambiguous selections |
| Naive RNNoise use has sample-rate, amplitude, tail and ownership risks | Process independent 480-sample 48 kHz native states in PCM16 amplitude units, compensate frame delay, flush tail, resample to 16 kHz and free state explicitly |
| Repeated ctypes loads can accumulate native-library references | Cache the loaded library while keeping independently freed per-stream states |
| Eager home-folder evaluation could fail despite valid APPDATA | Evaluate user-folder fallbacks lazily; never store credentials in shared temp |
| Duplicate launch produced a modal error instead of restoring | Auto-reset restore event, created before mutex election, retained across supervisor restarts; capture guard checked before showing |
| Separate desktops could collide on the same instance lock | Include canonical install path, window-station and desktop in both IPC names; use explicit ctypes signatures |
| Error bodies were discarded or could leak secrets if displayed | Bound bodies and return classified, fixed guidance; never show/log raw errors or echoed speech; Groq audio responses are bounded too |

**Primary engineer's interim assessment: 8.5/10.** New routing and recovery were implemented, but protocol edge cases, native ownership and integration verification still needed correction. Proceeded to the second round.

## Iteration 2 — validation and hardening

**Builder:** corrected the findings above; added source-separated streaming integration tests, cancellation races, mic ownership cleanup, provider protocol fixtures, native-library lifecycle checks and guarded IPC polling in the Qt event loop. Updated setup, no-console launching, local-model preparation, optional noise installation and target-machine acceptance instructions.

**Critic evidence:** the independent reviewer supplied 12 passing IPC/launcher tests and identified the obsolete-live-error race, which was fixed and tested. That reviewer did not complete a full independent sign-off for all new code. The primary engineer completed the remaining source review and portable checks; details and exact counts are in VALIDATION.md.

**Primary engineer's final assessment: 9.0/10.** Deductions: actual Windows setup/WASAPI/DPAPI/restore/capture paths untested (0.45); Hindi/Hinglish accuracy, GPU latency and noise-quality A/B unmeasured (0.30); real Gemini/Groq accounts, quota and long-session network behavior untested (0.15); broad independent final sign-off incomplete (0.10). This is an engineering assessment, not a measured accuracy score.

No fabricated 10/10 or “COMPLETED - PRODUCTION GRADE” certification is issued. The complete source and testable release are delivered for the user's laptop acceptance run. Repeating portable tests cannot resolve missing hardware/account evidence.

## Anti-Gravity report decisions

Implemented lazy settings lookup, silent duplicate-launch restore, supervisor-owned restore events, desktop isolation, model readiness, useful provider errors, and existing ONNX-before-Qt/nativeEvent workarounds. Retained PyAudioWPatch instead of replacing WASAPI support with vanilla PyAudio. Did not blanket-disable every Gemini 2.5 model, store credentials in shared temp, truncate raw error text before redacting, or kill unrelated Python processes. Model availability is checked through account discovery and explicit request failures; no silent model substitution occurs.

## Operating limits

- Display-affinity verification covers the Windows API contract, not universal recorder invisibility. Test actual capture paths.
- Complete-utterance ASR plus a separate text translator improves control; it does not prove ChatGPT/Gemini parity on this microphone.
- RNNoise defaults off and may harm quiet speech. It is not acoustic echo cancellation or reliable speaker separation.
- Continuous cloud captions send pauses and use separate mic/system sessions. Provider charges/quotas may apply.
- Queue drops and session renewal can create disclosed caption gaps. The HUD is not an archival meeting recorder.
- Cancellation invalidates results and stops streaming tasks; active native inference/downloads or HTTP reads can take time to finish cooperatively.
- The UI remains two modes. No interview/exam assistance, screenshot analysis or automatic answer generation was added.
