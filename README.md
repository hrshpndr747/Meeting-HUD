# Meeting HUD 1.5.0

A compact Windows desktop overlay for live captions and English translation. This release implements the approved text-focused HUD and Gemini streaming translation workflow.

## Update an existing installation

Read **UPGRADE.md** and use **ANTIGRAVITY-INSTALL-PROMPT.md**. Extract this release separately and let the supplied updater back up and update the existing source installation. Keep its virtual environment, downloaded models, saved settings, encrypted API keys and shortcut. This ZIP is source code, not a standalone EXE.

After installation, use **ANTIGRAVITY-TEST-PROMPT.md** with **TESTING-1.5.md**. **VALIDATION-1.5.md** states what was actually tested before delivery and what still requires the Windows laptop.

## Text-focused display

The default HUD is 600 × 116 logical pixels with a shared 15 pt Segoe UI caption font. Interim and completed captions use the same renderer and font; speech pauses do not change text size or window height. Live captions are off-white; English is light cyan. Long text advances within the bounded caption area. The default minimum width is 520 pixels to keep revealed controls usable.

Controls appear on pointer hover, keyboard use, source review, Settings or an error. Their space is reserved so hover does not shift captions. Drag the caption area to move the HUD; use its corner grip while controls are shown to resize it. Settings includes 11–22 pt text and two/three visible lines. Source text appears only when you choose Review source. Errors stay visible until cleared by a new action; full details are available in Settings and accessibility text.

## English translation

When both dictation recognition and English text generation use Gemini and **Translate while recording** is enabled:

1. Select English and press Record. The app connects to Gemini live recognition before opening the microphone.
2. Wait for Listening, then speak. Stable phrases are translated while recording continues; user-visible English streams into the HUD. Drafts may change when later words clarify the source.
3. Press Stop to flush the audio tail, finalize recognition and complete the remaining English. Copy becomes available when the translation completes.
4. Use Review source to inspect/edit recognition and Translate text to retry. A timeout or truncated stream retains source and any partial English, shows an error, and does not mark the draft complete.

Default streaming speech model: `gemini-3.5-transcribe-live`. The selected Gemini text model remains your existing choice. Account access must be verified on the laptop. Model-specific thinking controls request the lowest supported setting for known families; unrecognized models retain their default. Disable the fast-thinking option to compare quality/latency against defaults.

Cloud access remains explicit, with saved keys encrypted for the Windows account. Local/Groq stages or streaming disabled retain the existing record-then-translate workflow. Gemini text generation also streams in that workflow and for edited source text. No automatic provider switch is introduced. The original live-caption route remains separate.

Streaming removes avoidable waiting before the first English words; it does not promise instant complete sentences. Network, provider load, model/account access, speech ambiguity and source corrections affect observed speed and accuracy. Phrase updates may use more requests/tokens than one full-recording translation.

## Launch and controls

Use your existing shortcut or `Launch_HUD.vbs` after updating. `launcher.pyw` supervises restarts. `--paused` starts with capture paused. `main.py --smoke-ui` opens only the UI for installation checks; it starts no capture, network or model workers.

| Action | Shortcut / control |
| --- | --- |
| Live captions | Ctrl+1 / Live |
| English mode | Ctrl+2 / English |
| Record or Stop | Ctrl+R / Record or Stop |
| Cancel current English work | Escape / Cancel |
| Review/edit source | Ctrl+E / Review source |
| Settings | Ctrl+, / settings icon |
| Hide/restore globally | Ctrl+Shift+H / tray icon |

The local shortcuts require the HUD to have keyboard focus. Microphone capture in English mode requires Record. Opening Settings, cancelling or switching mode invalidates old work. Minimize stops an active recording; normal live-caption visibility behavior remains unchanged.

## Diagnostics and development

```powershell
.\.venv\Scripts\python.exe main.py --version
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe main.py --diagnose --report logs\diagnostics.json
```

The original dependency lock targets Windows x64/Python 3.11 and is unchanged. Original first-time setup instructions remain in `SETUP.md`; prefer the update guide for an existing installation. Do not add `--check-model` to a no-network installation check because it may load/download local models.

For the later real test phase, `run_latency.bat` enables bounded metadata-only timing logs. `summarize_latency.py --logs logs --out logs\latency-summary.json` reports request timing quantiles and event counts. It does not infer semantic accuracy or exact physical audio-to-screen latency. See the test guide for interpretation.

Capture exclusion remains implemented with the original protected-parent-window approach, including inline settings and no native tooltips. Native Windows protection and driver behavior must be verified on the target laptop.
