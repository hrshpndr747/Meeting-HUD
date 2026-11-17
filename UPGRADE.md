# Upgrade to Meeting HUD 1.5.0

This is an updated **source release** for the existing Windows/Python installation. It is not a standalone EXE installer. Install first, then test real translation separately.

## Recommended path

Use `ANTIGRAVITY-INSTALL-PROMPT.md`. Extract `Meeting-HUD-1.5.0.zip` to a separate staging directory, give Antigravity that directory, and let it locate the installation your current shortcut uses. Do not delete or overwrite your existing folder manually.

`Update-MeetingHUD.ps1 -TargetDirectory "C:\your\existing\Meeting-HUD"` validates release checksums, refuses an overlapping source/target and running target processes, and backs up every existing application file it will replace. It also backs up the existing Windows user settings folder, leaving encrypted credentials encrypted. It then updates only manifest-listed application files and verifies the installed version. If copying/version validation fails, it attempts a scoped rollback.

It preserves your virtual environment, model downloads, optional native dependencies, environment files, logs and saved preferences. The original ZIP remains a separate backup of the supplied 1.4.0 source. Source files you customized locally are backed up but may be replaced by this update; inspect any such changes before running the updater.

## Installation checks

From the existing installation folder, using its environment:

```powershell
.\.venv\Scripts\python.exe main.py --version
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe main.py --diagnose --report logs\installation-diagnostics.json
.\.venv\Scripts\python.exe main.py --smoke-ui
```

Expected app version: **Meeting HUD 1.5.0**. The smoke UI starts no capture, network or model workers. Do not add `--check-model` during installation checks. Close the smoke UI afterward. Keep the existing shortcut; its target does not need to move.

The source update adds no new dependency requirements. `requirements.txt` retains the original Windows 3.11 pins. A fresh environment may need installation repair, but an existing working environment should not be rebuilt blindly. The development checks were run on Linux with different dependency versions; consult `VALIDATION-1.5.md`.

## Activate the approved streaming behavior

Existing settings and keys remain in place. When you authorize real testing, check Settings:

- “Recognize my speech”: Gemini API. The existing batch speech model stays available for the fallback workflow.
- “English translation”: Gemini API, with an available text-generating model selected for your account.
- “Translate while recording”: enabled.
- Streaming speech model: `gemini-3.5-transcribe-live` by default; verify account access during testing.
- “Use the lowest supported Gemini thinking setting”: enabled. Disable to benchmark model defaults if needed.
- Cloud access: retain your existing explicit choice. It must be enabled to use Gemini.

The live-caption provider is independent and remains configured as before. With local or Groq stages, or streaming disabled, the app retains the record-then-translate workflow. Gemini text generation still streams in that workflow and when translating edited source text.

## Rollback

Close the HUD and supervisor first. The updater prints the timestamped backup directory and writes `update-record.json` there. Its `application` folder contains the original versions of replaced files. Restore only entries marked `existed: true` to the recorded target; remove only update-created entries marked `existed: false`. Do not remove `.venv`, models, settings, logs or unrelated files. Verify restored file hashes against the backup. If settings were subsequently changed, restore the backed-up user settings only after considering those changes; encrypted keys remain tied to the same Windows user.

No automated Windows updater execution occurred in the development environment. Antigravity must review and run the supplied script on your laptop, using the installation prompt.
