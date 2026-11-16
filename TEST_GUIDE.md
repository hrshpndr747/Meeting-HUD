# Laptop acceptance tests

Use the new shortcut and a headset. These tests require your actual Windows machine; the portable suite cannot establish speech accuracy, GPU latency or capture exclusion.

## 1. Startup and controls

1. Run setup in a fresh folder whose path contains spaces. Confirm `pip check` succeeds.
2. Double-click `Meeting HUD.lnk`: only the HUD should appear, with no persistent or flashing CMD. `run.bat` may flash a console and is not the zero-console entry point.
3. Open a second instance in the same folder: it should direct you to the existing instance.
4. Test minimize, tray restoration, Ctrl+Shift+H and Close. Reopen from the shortcut.
5. Open/close Settings near each screen edge and at Windows scaling of 100%, 125% and 150%. Controls must stay accessible and the previous compact size must return.
6. Save a different model and confirm the supervisor restarts. Observe any CPU fallback message. Run the local model diagnostic for both stages.

## 2. Manual translation

Translation must show MIC OFF before Record. Speak while idle: no source/English should appear. Press Record, say a sentence, pause for two seconds, finish it, then Stop → Translate. Verify one complete source passage includes both sides of the pause and its final word.

Try these examples and your own names/technical terms:

| Spoken source | Expected meaning, allowing natural paraphrase |
| --- | --- |
| मैं कल नहीं आ सकता। | I cannot come tomorrow. |
| Invoice पंद्रह हजार का है, पचास हजार का नहीं। | The invoice is for fifteen thousand, not fifty thousand. |
| कल तीन बजे Project Lotus की review meeting रखो। | Schedule the Project Lotus review meeting for three tomorrow. |
| मुझे लगता है delivery शुक्रवार तक हो सकती है। | I think delivery may be possible by Friday. |
| राहुल ने कहा कि budget approve नहीं हुआ है। | Rahul said the budget has not been approved. |

Check recognition separately from translation: if the source is wrong, correct it and click Translate text. Verify that English changes to match your correction and the copy button never copies an obsolete result. Test silence, soft speech, Hinglish, fast speech, numbers, negation and a 45–60 second utterance. The default 60-second cap should stop/submit automatically, with no lost ending.

Cancel during recording, recognition and translation. Old results must not appear after cancellation or a mode change. The mic should close after Stop/Cancel. An in-flight native call may take time to return; the UI should remain responsive while waiting.

## 3. Live captions and devices

Play meeting audio through the Windows default output and speak into the default mic. Expect MEETING and YOU lines. Pause/resume; unplug/reconnect the headset; switch Windows default devices. Confirm recovery or an actionable notice, without crashing the GUI. Use a headset when checking duplicates.

Compare `small`, `medium` and `large-v2` on the same two-minute sample. Record approximate end-to-text latency and missing phrases. The live queue intentionally drops old chunks when the model cannot keep up; larger models may improve individual phrases while worsening coverage.

## 4. Independent models and privacy

- All-local: leave cloud disabled, cache the models, set offline mode, disconnect the network and test both modes. Ollama stays running locally.
- Mixed: live Local `large-v2`, dictation Local `large-v3`, English Gemini with your chosen returned model. Confirm Settings keeps these choices independent after restart.
- Clear/replace the API key and load models. Old discovery results must not overwrite a newly selected provider or key.
- Use an invalid/revoked key; the HUD should show a concise error without exposing key or response contents.
- Verify preferences contain no API key. DPAPI files should be binary ciphertext; test Forget saved key. Check logs/diagnostics for accidental sensitive content before sharing.
- Generation may consume provider quota or incur provider fees when cloud is enabled. Skip cloud generation tests if you require strictly zero paid usage.

## 5. Capture exclusion matrix

Put a distinctive harmless sentence in the HUD. In each test verify it is visible on the physical monitor but absent from the captured result on another device or in the saved file. A successful API readback alone is insufficient.

| Capture path | Test |
| --- | --- |
| Windows Snipping Tool | Rectangle, window and full-screen snips; screen recording if available |
| Teams | Share entire screen and a specific window; inspect from a second participant device |
| Zoom | Share screen and selected window; inspect recording and remote participant view |
| Google Meet / browser WebRTC | Share entire screen, window and tab; inspect remotely |
| OBS | Test Display Capture and each relevant Window/Game Capture method separately |

Repeat while Settings and its model list are open, after resize/hide/restore, and after display/DPI changes. The tray icon and generic startup-error dialogs are OS UI and may be visible; no transcript or key is placed in them. If any capture path includes HUD content, treat that path as unsupported and use Ctrl+Shift+H to hide it locally. Windows display affinity cannot guarantee exclusion from every recorder, capture driver or physical camera.

## Feedback record

Record app version, selected model for each stage, GPU/CPU indication, approximate latency, source text, expected meaning, actual English and which capture mode was tested. Use non-sensitive examples. Attach a reviewed diagnostic report if needed. Accuracy feedback is more useful when source-recognition errors are separated from translation errors.

Portable suite (no hardware or live provider requests):

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## v1.4 microphone and Gemini acceptance

1. **Selected microphone:** load microphones in Settings, choose the headset and Test 5 sec. Speak softly, normally and loudly. Confirm quiet/clipping guidance is sensible. Unplug it, reconnect it, and reorder/default-switch devices. The named selection must not quietly become a different mic. Rename identical device names in Windows if the app reports ambiguity.
2. **Explicit testing:** close Settings, switch modes, minimize, and close the app during the mic test. Confirm the microphone releases. Start Record immediately after leaving Settings; no competing test/live stream may own the mic. Test capture is local and never submitted to any provider.
3. **RNNoise A/B:** install through setup_noise.bat. Use the same 20 Hindi/Hinglish sentences with it off/on under fan noise, keyboard noise and room conversation. Include quiet word endings. Score recognized words before scoring translation. Do not assume suppression improves competing-speaker audio. Confirm long recordings and the final word survive Stop. Remove the DLL with the app closed and confirm enabling suppression gives a setup/disable message, while suppression off still works.
4. **Gemini speech selectors:** with your own key, independently select a live transcribe-live model, a dictation Transcribe or compatible Flash/Pro model, and a text translation model. Save and restart. Verify all three remain independent and keys are masked. This is an optional provider-account test; keep all-local selections if you require no API charges.
5. **Manual Hindi:** click Record and speak a complete thought with pauses. Nothing should submit at a natural pause. Stop should produce editable Hindi/Hinglish first, then English preserving names, numbers, dates and negation. Correct one source word and Translate text again; audio must not be re-recorded or reinterpreted.
6. **Gemini live:** confirm MEETING and YOU remain separated, speculative text updates in place and final phrases appear once. Never accept a generated assistant reply as a caption. Pause/mode change/Settings should stop old uploads/results. Test internet disconnect/reconnect, slow links and a meeting longer than 20 minutes. Reconnect notices must disclose gaps; old audio must not be replayed as current speech.
7. **Provider failures:** invalid key, inaccessible/retired model, wrong model family and exhausted quota must produce actionable errors. Raw error bodies, keys and recognized speech must not enter logs. Local models remain selectable; no paid or different model should activate automatically.
8. **Repeated launch:** open the shortcut twice while loading, while hidden, while visible, and repeatedly across Save and restart. Exactly one HUD should remain for that project/desktop. A click during restart should restore the new protected window. If practical, verify another isolated Windows desktop does not consume its restore request. Do not terminate unrelated Python processes.
9. **Fresh setup:** extract to a new path with spaces, create a new venv, install, prepare selected local models, and launch via the newly generated shortcut. Keep the first model download distinct from an ordinary startup crash. Do not copy old .venv/.lnk files.
10. **Capture protection:** repeat the existing Snipping Tool/Teams/Zoom/Meet/OBS checklist after changing Settings and after shortcut restore. A successful Win32 return/readback alone is not sufficient evidence of exclusion in every recorder.

Record three separate measurements: source transcription errors, English meaning errors, and time from Stop to final English. Use identical phrases and input conditions for model comparisons. Neither synthetic tests nor a review score establish cloud-equivalent recognition on your microphone.
