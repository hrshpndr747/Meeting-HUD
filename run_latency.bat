@echo off
setlocal
cd /d "%~dp0"
echo Close Meeting HUD completely before continuing.
echo Timing logs contain metadata only; no speech text, audio or API keys.
pause
if not exist ".venv\Scripts\python.exe" (
    echo Existing Python environment not found. Complete installation first.
    pause
    exit /b 1
)
set "HUD_LATENCY_LOG=1"
".venv\Scripts\python.exe" launcher.pyw --paused
endlocal
