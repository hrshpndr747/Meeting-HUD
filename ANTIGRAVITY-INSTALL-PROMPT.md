# Antigravity prompt — install the approved Meeting HUD update

Copy the prompt below into Antigravity on your Windows laptop. Attach or give it the path to the extracted **Meeting-HUD-1.5.0** folder. Extract the ZIP to a separate staging folder first; do not extract over your existing installation.

---

Update my existing Meeting HUD Windows application to the supplied 1.5.0 release. I have approved this update. Complete installation and offline/UI checks first. Do not begin live microphone, API, translation accuracy or latency testing yet.

1. Locate the actual existing installation using my shortcut target and its working directory. Confirm `main.py`, `config.py`, `launcher.pyw`, `ui_overlay.py` and `.venv\Scripts\python.exe` belong to that installation. Confirm the supplied staging folder contains `Update-MeetingHUD.ps1`, `release-manifest.json`, `UPGRADE.md` and the new code. If there are multiple plausible installations, ask me to identify which I use. Do not guess or create a second active app. If this is an EXE-only installation, stop and report that the source updater does not support it.

2. Read `UPGRADE.md`, `VALIDATION-1.5.md` and the updater script. Inspect the existing environment without printing API keys, environment-file contents or credential contents. Record the old app version, Python version, shortcut target, package health and whether there are local code modifications. If there are local feature changes beyond settings, preserve them in the backup and explain any conflict before overwriting those specific changes. Do not copy my key into a prompt, source file, command line or report.

3. Close the running HUD normally. Confirm its main and supervisor processes have exited. Do not terminate unrelated Python processes. Keep the new staging folder outside the existing installation. Run the supplied updater with the verified existing folder:

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\PATH\TO\Meeting-HUD-1.5.0\Update-MeetingHUD.ps1" -TargetDirectory "C:\PATH\TO\EXISTING\Meeting-HUD"
   ```

   Replace these two placeholders with observed paths; never run the placeholders literally. Use the policy override only for this process, not a permanent machine-wide change. The updater creates and verifies a timestamped backup before replacing release files. Confirm it succeeded and record the backup path. Preserve the existing `.venv`, downloaded models, optional RNNoise/CUDA files, environment files, logs, unrelated files and Windows user settings/DPAPI credentials. Do not delete the backup or relocate the virtual environment. If the script refuses the target or reports an error, diagnose it without bypassing its path or checksum checks.

4. Use that installation's existing `.venv\Scripts\python.exe` for checks, not a global interpreter. No dependency changes are required by the new source. Check Python is compatible (3.11 is the original Windows target), run `main.py --version`, `python -m pip check`, `python -m unittest discover -s tests -v`, and `main.py --diagnose --report logs\installation-diagnostics.json` from the actual app directory. Do not add `--check-model`; that can load or download models. If a dependency is missing, identify it, preserve a package inventory, and repair only what is needed in this environment; do not blindly recreate the environment or replace working GPU packages. Treat any test failure as an installation blocker until understood.

5. Run `main.py --smoke-ui` for the visual smoke check. This opens the actual protected HUD without starting microphone, network or model workers. Check live/English mode buttons, the default text-only view, controls on hover, source drawer, Settings, resize, minimize/restore and close. The default size should be 600 × 116 logical pixels at 15 pt, with no size jump when changing modes. Keep capture protection enabled. Do not save settings or load model lists during this check. If capture protection cannot initialize on this Windows build, report it instead of disabling it. Close the smoke-check window normally afterward.

6. Confirm the existing shortcut still points to the same installation and launcher. Do not start the normal app for audio/API checks in this phase. Do not silently enable cloud access, change model selections or replace saved keys. The new streaming route applies when both speech recognition and English text translation use Gemini and “Translate while recording” is enabled. Review those non-secret selections and report whether this is already configured; model/account access will be verified in the later test phase.

7. Give me a concise installation report: installed path and version, backup path, Python/package check results, test counts and failures, UI checks, settings/model preservation, any required follow-up, and exact launch instructions. If the update cannot pass these checks, leave the app closed, preserve the backup, and provide or perform a scoped rollback using the backup record. Do not delete or overwrite unrelated files during rollback. Stop here and wait for my next instruction before real translation testing.

---

After Antigravity finishes, share its installation report in this chat. Keep the backup until Windows and translation testing are complete.
