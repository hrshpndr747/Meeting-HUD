# Antigravity prompt — test translation after installation

Use this only after installation and the UI-only smoke check have passed. The first prompt installs the update; this prompt starts a separate test phase that uses your microphone and selected Gemini account.

---

Test my installed Meeting HUD 1.5.0 for English translation responsiveness, reliability and Hindi/Hinglish accuracy. Read `TESTING-1.5.md` and `VALIDATION-1.5.md` first. Keep the installed baseline unchanged while gathering evidence. Do not claim sub-second translation or complete accuracy without measurements.

1. Confirm the active installation and version, the verified backup, Python/environment health and passing offline tests. Record Windows version, display scaling, microphone selection, noise-suppression setting, network type, chosen live speech and English text model IDs, streaming setting and fast-thinking setting. Record whether this is a cold start or an already-open session. Keep API keys and credential files private; use the app's existing settings. Do not ask me to paste keys into this chat.

2. Close any existing HUD instance. Archive prior timing files locally under a named previous-run directory instead of deleting them. Start `run_latency.bat` from the installed folder. It enables metadata-only timing logs and starts capture paused. Check Settings without saving unintended changes. For the intended configuration, both dictation speech and text translation use Gemini, streaming is enabled, and cloud use is enabled by my existing choice. Verify the selected text model supports text generation and the selected live model is available to my account. If unavailable, show the error and let me choose an available model; never silently swap providers.

3. Select English mode, which leaves the microphone off until Record. Guide me to press Record and wait for “Listening…” before speaking. I will speak the controlled samples from `TESTING-1.5.md` in Hindi/Hinglish. Use at least 20 short samples and five longer passages; repeat a fixed subset three times and keep the wording consistent. Include names, quantities, dates, negation, self-correction, technical English words and sentence pauses. Note sample IDs and recording epochs from timing events without putting spoken text into the automatic log. Test a cold first request separately from warm requests. Stop each recording and verify the final phrase appears and processing ends. Separate recording time from provider translation time.

4. Measure stages separately. Use `translation_requested`, `translation_first_text`, `translation_completed`, `source_interim`, `source_final`, `capture_started`, `capture_stopped`, `translation_ui_updated` and failure/cancellation events. Match by run ID, epoch, request ID and segment/revision. `translation_first_text.elapsed_ms` is request-start to first English callback, including connection/retry time; `source_to_first_ms` starts at an ASR hypothesis revision, not the beginning of speech. `translation_ui_updated` marks arrival at the GUI handler, not the exact physical screen paint. Stop-to-complete starts at capture end. Distinguish revisable draft requests from final-source requests, and show counts/failures alongside P50 and P95. Do not interpret a partial caption as completed translation.

5. Run the included summary command using this installation's interpreter:

   ```powershell
   .\.venv\Scripts\python.exe summarize_latency.py --logs logs --out logs\latency-summary.json
   ```

   Create a separate per-sample table with spoken sample ID, correct recognition yes/no, final meaning preserved yes/no, first-English observation, completion result and error notes. I should review the displayed source and English for semantic accuracy, especially negation and numbers. If recording the screen for timing, obtain my explicit consent for the recording and scope it to non-sensitive test content. The HUD's capture protection may exclude it; do not disable that protection to obtain a screenshot or video. A controlled manually timed observation is acceptable but must be labelled approximate.

6. Test cancellation during connection, during speech and while English is streaming. Immediately start a new recording; old text must never overwrite it. Test Stop with a last word just before release, short and long pauses, a 30–60 second passage, corrected recognized text followed by Translate text, mode switches, Settings during a recording, and reconnect recovery. Test one controlled temporary network interruption with my awareness. Simulate quota/timeout/cutoff responses using the existing offline tests; do not deliberately exhaust paid quota or expose the real key in an error. Partial English must remain a draft with a visible error, and the recognized source must remain available for retry.

7. Check the text-only UI at Windows 100%, 125%, 150% and 200% scaling as practical: same font size before/after pauses and finalization, no caption reflow from toolbar hover, no overlapping controls, bounded long-text display, keyboard access, source drawer, tray/hotkey restore and protected settings. Report which scales were actually exercised. Restore my preferred display scaling after testing. Never disable capture protection to make a test pass.

8. Only after the baseline is measured, offer an isolated comparison: streaming enabled vs disabled with the same Gemini text model, then fast thinking enabled vs model default. Change one setting at a time and restart through the existing launcher so saved changes apply. The streaming-off route uses the existing batch speech model, so distinguish recognition-route differences from text-provider differences. Keep identical human samples and document model IDs. Compare observed wait for first English as well as completion, failure rate and accuracy. Do not switch to a different text model in the same comparison. Restore my selected configuration after the comparison.

9. Produce `translation-test-report.md`, `latency-summary.json` and a sanitized copy of the timing logs from this test run. Include test conditions, sample counts, P50/P95, failures, cancellations, accuracy observations, worst cases, UI results, unresolved issues and suggested next changes. Do not include raw audio, real conversation transcripts, API keys, environment files, DPAPI blobs or unrelated logs. Review the bundle for secrets before sharing it. Keep originals local. Do not patch the app until we review this baseline. Stop and give me the report to bring back to ChatGPT.

---

No instant-translation claim is implied by this prompt. It is designed to locate the remaining delay and distinguish a fast draft from an accurate completed translation.
