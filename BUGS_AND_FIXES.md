> Historical 1.4 documentation retained for reference. For the 1.5.0 update, use README.md, UPGRADE.md, TESTING-1.5.md and VALIDATION-1.5.md. Old UI descriptions and prior test counts below do not describe this release.

# Meeting HUD — Comprehensive Bugs & Issues Resolution Report

This document details all technical issues, runtime failures, architectural bugs, and integration defects encountered in **Meeting HUD**, along with their root causes, implementation details, and verification steps.

---

## Summary Matrix

| ID | Issue Description | Root Cause | Impact | Resolution Summary | Files Modified |
|---|---|---|---|---|---|
| **BUG-01** | Uninitialized Python environment & missing native ML dependencies | Virtual environment `.venv` was absent; PyAudio, faster-whisper, ctranslate2, torch, and PyQt6 were not installed | Tool could not launch (`ModuleNotFoundError`) | Built isolated Python 3.11 `.venv`, installed pinned dependencies and PyAudio wheel | `.venv/`, `requirements.txt` |
| **BUG-02** | `RuntimeError: Could not determine home directory` in `preferences.py` | Eager evaluation of `Path.home()` in default expression caused fatal crash when `os.environ` lacked home directory | Test suites and sandboxed runs crashed on import | Wrapped path resolution in guarded `try/except RuntimeError` with fallback | [`preferences.py`](file:///c:/Users/Harsh/Desktop/temp/meeting_hud/preferences.py) |
| **BUG-03** | Missing offline Whisper & Ollama models | Faster-Whisper weights (`small`, `large-v2`, `large-v3`) and Ollama LLM were not cached locally | Real-time speech transcription and offline translation stalled | Pre-cached faster-whisper weights in `models/`; started Ollama daemon and pulled `qwen3:4b-instruct` | `models/`, Ollama service |
| **BUG-04** | "Meeting HUD is already running" modal error dialog on shortcut double-click | `launcher.pyw` detected mutex collision on subsequent clicks and showed a blocking `MessageBoxW` popup instead of raising the window | Users could not reopen or restore the application after minimization | Replaced error popup with Win32 named restore event (`Local\MeetingHUD_Restore_...`) & `AllowSetForegroundWindow` | [`launcher.pyw`](file:///c:/Users/Harsh/Desktop/temp/meeting_hud/launcher.pyw), [`main.py`](file:///c:/Users/Harsh/Desktop/temp/meeting_hud/main.py) |
| **BUG-05** | UI disappearance & restore timeout during "Save and restart" | `main.py` destroyed the restore event upon exiting; during heavy ML module re-import, incoming clicks timed out after 3s | Double-clicking during restart failed and threw modal error dialog | Moved restore event creation to `launcher.pyw` to persist across restart loops (exit code 75) | [`launcher.pyw`](file:///c:/Users/Harsh/Desktop/temp/meeting_hud/launcher.pyw), [`ui_settings.py`](file:///c:/Users/Harsh/Desktop/temp/meeting_hud/ui_settings.py) |
| **BUG-06** | Gemini translation failed with HTTP 404 (`NOT_FOUND`) | Google retired `gemini-2.5-flash-lite` and `gemini-2.5-flash` on `v1beta` endpoint for new projects/keys | Text translation failed completely when Gemini was selected | Migrated active settings to `gemini-3.5-flash-lite`; verified HTTP 200 end-to-end | `%APPDATA%\MeetingHUD\settings.json` |
| **BUG-07** | Discontinued models listed in Settings dropdown | Google API `v1beta/models` still returned retired `gemini-2.5-*` models in raw discovery JSON | Users could select non-functional models from the UI | Filtered out `gemini-2.5-` series models in `fetch_models()` | [`model_catalog.py`](file:///c:/Users/Harsh/Desktop/temp/meeting_hud/model_catalog.py), [`tests/test_providers.py`](file:///c:/Users/Harsh/Desktop/temp/meeting_hud/tests/test_providers.py) |
| **BUG-08** | Provider error details discarded by network layer | `network_io.py` discarded response bodies on non-200 status codes, obscuring Google's deprecation message | Users only saw generic "HTTP 404" with no explanation | Updated `request_json()` to parse bounded error JSON and display provider error text safely | [`network_io.py`](file:///c:/Users/Harsh/Desktop/temp/meeting_hud/network_io.py), [`text_translation.py`](file:///c:/Users/Harsh/Desktop/temp/meeting_hud/text_translation.py) |
| **BUG-09** | Desktop windowstation isolation blocking interactive launches | Background tasks running under isolated desktop (`exebox`) held session mutex `Local\MeetingHUD_...` | User double-clicking shortcut on `Default` desktop could not see or start the tool | Terminated orphaned background processes; ensured clean single-instance lifecycle | Process lifecycle & `launcher.pyw` |

---

## Detailed Bug Breakdown

### 1. Environment & Dependency Setup (BUG-01)
- **Symptom**: Meeting HUD could not be launched; dependencies like `PyQt6`, `pyaudio`, `faster-whisper`, and `ctranslate2` were not installed.
- **Root Cause**: Virtual environment `.venv` was missing. On Windows x64, PyAudio requires compiling PortAudio C extensions unless installed via a pre-built wheel.
- **Solution**:
  1. Initialized Python 3.11 virtual environment at `.venv`.
  2. Downloaded and installed `pyaudio-0.2.14-cp311-cp311-win_amd64.whl`.
  3. Installed pinned `requirements.txt` and `requirements-gpu.txt`.
  4. Verified runtime bootstrap via `.\.venv\Scripts\python.exe -m unittest discover -s tests`.

---

### 2. Eager Fallback Evaluation in `preferences.py` (BUG-02)
- **Symptom**: `RuntimeError: Could not determine home directory` occurred during unit testing and sandboxed executions.
- **Root Cause**:
  In [`preferences.py`](file:///c:/Users/Harsh/Desktop/temp/meeting_hud/preferences.py#L13-L21), the fallback path evaluation was:
  ```python
  def settings_path() -> Path:
      return Path(os.getenv("APPDATA", str(Path.home() / ".config"))) / "MeetingHUD" / "settings.json"
  ```
  Python eagerly evaluates arguments in function calls. When tests patched `os.environ` with `{}` or ran under accounts without an initialized user profile, `Path.home()` failed immediately even if `APPDATA` was present or should have fallen back.
- **Solution**:
  Guarded the `Path.home()` resolution in a `try/except RuntimeError` block, defaulting to `tempfile.gettempdir()` if home is unavailable:
  ```python
  def settings_path() -> Path:
      appdata = os.getenv("APPDATA")
      if appdata:
          return Path(appdata) / "MeetingHUD" / "settings.json"
      try:
          base = Path.home() / ".config"
      except RuntimeError:
          base = Path(tempfile.gettempdir())
      return base / "MeetingHUD" / "settings.json"
  ```

---

### 3. Model Pre-caching & Local Ollama Setup (BUG-03)
- **Symptom**: Initial transcription and translation took a long time or failed offline; Ollama connection error occurred.
- **Root Cause**: Whisper models (`small`, `large-v2`, `large-v3`) were not pre-downloaded in `models/`, and Ollama daemon was not running locally.
- **Solution**:
  1. Pre-downloaded and verified HuggingFace Faster-Whisper weights (`models--mobiuslabsgmbh--faster-whisper-large-v3-turbo`, `small`, etc.).
  2. Verified Ollama service at `http://127.0.0.1:11434` and pulled `qwen3:4b-instruct`.
  3. Validated offline transcription and translation pipelines without network egress.

---

### 4. Mutex Collision & "Already Running" Dialog on Shortcut Double-Click (BUG-04)
- **Symptom**:
  Double-clicking `Meeting HUD.lnk` while Meeting HUD was running or minimized displayed an error popup:
  > *"Meeting HUD is already running. Click its tray icon or press Ctrl+Shift+H to restore it."*
- **Root Cause**:
  [`launcher.pyw`](file:///c:/Users/Harsh/Desktop/temp/meeting_hud/launcher.pyw) used `CreateMutexW` for single-instance enforcement. When `GetLastError() == 183` (`ERROR_ALREADY_EXISTS`), it called `show_error()` and exited. Users expect double-clicking a running app's icon to bring the existing window to the front, not to display an error modal.
- **Solution**:
  1. Created a named Win32 Event (`Local\MeetingHUD_Restore_<hash>`).
  2. When `already_running` is detected, the new launcher process calls:
     ```python
     ctypes.windll.user32.AllowSetForegroundWindow(0xFFFFFFFF)
     event = kernel.OpenEventW(0x0002, False, event_name)
     if event:
         kernel.SetEvent(event)
         kernel.CloseHandle(event)
     ```
  3. In [`main.py`](file:///c:/Users/Harsh/Desktop/temp/meeting_hud/main.py#L288-L302), the Qt event loop (`tick()`) detects the signaled event:
     ```python
     if restore_event and kernel.WaitForSingleObject(restore_event, 0) == 0:
         kernel.ResetEvent(restore_event)
         hwnd = int(window.winId())
         user32.ShowWindow(hwnd, 9)  # SW_RESTORE
         user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
         user32.SetForegroundWindow(hwnd)
         window.show()
         window.raise_()
         window.activateWindow()
         self.notice("Meeting HUD active · Ctrl+Shift+H toggles visibility")
     ```
  4. Removed the modal error box so subsequent launches cleanly raise the HUD.

---

### 5. Restore Event Lifetime & Restart Resiliency (BUG-05)
- **Symptom**:
  Clicking "Save and restart" in Settings caused the UI to close. If the user double-clicked `Meeting HUD.lnk` while models were reloading in the background, the error popup reappeared.
- **Root Cause**:
  `restore_event` was previously created inside `main.py` *after* all heavy ML libraries (`ctranslate2`, `torch`, `faster_whisper`) were imported. During a restart (exit code 75), there was a 3-8 second window where `main.py` had exited and the new instance was still importing modules. Double-clicking during this interval resulted in `OpenEventW` failing, falling through to the error box.
- **Solution**:
  1. Moved `restore_event = kernel.CreateEventW(None, True, False, event_name)` directly into [`launcher.pyw`](file:///c:/Users/Harsh/Desktop/temp/meeting_hud/launcher.pyw#L45-L48).
  2. Because `launcher.pyw` survives `main.py` restarts (looping on exit code 75), `restore_event` exists continuously from the very first millisecond.
  3. If double-clicked while `main.py` is initializing, the event remains signaled, and `main.py` raises the window as soon as its first tick executes.

---

### 6. Google Gemini HTTP 404 Discontinuation Error (BUG-06)
- **Symptom**:
  Text translation failed with:
  > *"Translator returned HTTP 404. Check the key/model in Settings."*
- **Root Cause**:
  Google retired older models (`gemini-2.5-flash` and `gemini-2.5-flash-lite`) on the `v1beta` endpoint for new projects/keys. API requests to `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent` returned HTTP 404 `NOT_FOUND`:
  > *"This model models/gemini-2.5-flash-lite is no longer available to new users. Please update your code to use models/gemini-3.5-flash-lite for the latest features and improvements."*
  The user's configuration in `%APPDATA%\MeetingHUD\settings.json` was set to `"gemini_model": "gemini-2.5-flash-lite"`.
- **Solution**:
  1. Updated `%APPDATA%\MeetingHUD\settings.json` to use `"gemini_model": "gemini-3.5-flash-lite"`.
  2. Tested live translation with Hindi/English test phrases; verified HTTP 200 and accurate translation.

---

### 7. Filter Deprecated Models in Catalog Discovery (BUG-07)
- **Symptom**:
  When users clicked "Load models" in the Settings panel under Gemini, discontinued models like `gemini-2.5-flash-lite` and `gemini-2.5-flash` appeared in the list, allowing users to select broken models.
- **Root Cause**:
  Google's `GET /v1beta/models` endpoint still enumerates retired models for backward-compatibility with older projects. [`model_catalog.py`](file:///c:/Users/Harsh/Desktop/temp/meeting_hud/model_catalog.py#L87-L93) only filtered out non-text models (e.g. `image`, `tts`, `robotics`), leaving retired 2.5 models in the selection list.
- **Solution**:
  Updated `fetch_models()` in `model_catalog.py` to explicitly exclude `gemini-2.5-` series models:
  ```python
  if request.provider == "gemini":
      name = row.get("name", "").removeprefix("models/")
      usable = ("generateContent" in row.get("supportedGenerationMethods", [])
                and name.startswith("gemini-")
                and not any(x in name for x in ("image", "tts", "robotics", "computer-use", "live"))
                and not name.startswith("gemini-2.5-"))
  ```
  Added unit tests in [`tests/test_providers.py`](file:///c:/Users/Harsh/Desktop/temp/meeting_hud/tests/test_providers.py#L43-L52) to prevent regressions.

---

### 8. Enhanced Error Diagnostics & Key Redaction (BUG-08)
- **Symptom**:
  Any HTTP error (400, 403, 404, 429) from cloud providers produced a vague message: *"Translator returned HTTP {code}. Check the key/model in Settings."* Users had no insight into the provider's explanation.
- **Root Cause**:
  In [`network_io.py`](file:///c:/Users/Harsh/Desktop/temp/meeting_hud/network_io.py#L17-L29), if `response.status_code != 200`, the body was not consumed (`return response.status_code, None, response.headers`).
- **Solution**:
  1. Updated `request_json()` in `network_io.py` to read up to 16KB of response body on error responses and parse JSON payloads.
  2. In [`text_translation.py`](file:///c:/Users/Harsh/Desktop/temp/meeting_hud/text_translation.py#L96-L108), extracted provider error messages (e.g. `data["error"]["message"]`), sanitized whitespace, redacted API keys, and raised descriptive exceptions:
     ```python
     if err_msg and isinstance(err_msg, str):
         clean_msg = " ".join(err_msg.split())[:200]
         if self.config.gemini_api_key:
             clean_msg = clean_msg.replace(self.config.gemini_api_key, "[REDACTED]")
         if self.config.groq_api_key:
             clean_msg = clean_msg.replace(self.config.groq_api_key, "[REDACTED]")
         raise TranslationError(f"Translator error (HTTP {code}): {clean_msg}")
     ```

---

### 9. Desktop WindowStation Concurrency Conflict (BUG-09)
- **Symptom**:
  Double-clicking `Meeting HUD.lnk` did not produce any visible window on the user's monitor.
- **Root Cause**:
  Background subagents or test runners launched under an automated desktop windowstation (`WinSta0\exebox-...`) instead of the user's interactive desktop (`WinSta0\Default`). Because the mutex `Local\MeetingHUD_...` is scoped to the Windows login session, the background process held the mutex, causing launches from the interactive desktop to signal the invisible `exebox` instance.
- **Solution**:
  1. Terminated all lingering background Python processes holding the mutex.
  2. Verified that interactive launches from Windows Explorer / Desktop execute on `WinSta0\Default` with unobstructed access to the screen, hotkeys, and display affinity.

---

## Verification & Test Results

The test suite was run against the patched codebase:
```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```
- **Total Tests**: 99 unit tests
- **Result**: `OK` (0 failures, 0 errors)
- **Coverage**:
  - Windows DPAPI credential encryption round-trips
  - Stealth capture exclusion (`WDA_EXCLUDEFROMCAPTURE = 0x11`)
  - Gemini, Groq, and Ollama catalog discovery and stage filtering
  - Deprecated model filtering (`gemini-2.5-*`)
  - Provider error body parsing and credential redaction
  - Audio chunk resampler and VAD boundary detection
  - Pipeline state transitions and mode switches

---

## Operating Guidelines for the User

1. **Launching**:
   - Double-click **`Meeting HUD.lnk`** in `c:\Users\Harsh\Desktop\temp\meeting_hud`.
   - The HUD will appear as a dark floating pill above the taskbar at the bottom center of the monitor.
2. **Restoring / Unhiding**:
   - Double-clicking `Meeting HUD.lnk` at any time brings the running HUD to the foreground.
   - Alternatively, press <kbd>Ctrl</kbd> + <kbd>Shift</kbd> + <kbd>H</kbd> to toggle visibility.
   - Or click the Meeting HUD icon in the Windows notification area (system tray).
3. **Translation**:
   - Switch to **Translation** mode.
   - Click **Record**, speak in Hindi/Hinglish/multilingual, then click **Stop & Translate**.
   - The HUD automatically translates to English using the configured `gemini-3.5-flash-lite` model.
