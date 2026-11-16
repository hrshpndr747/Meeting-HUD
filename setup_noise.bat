@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run setup.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" install_noise.py
set "HUD_NOISE_EXIT=%errorlevel%"
pause
exit /b %HUD_NOISE_EXIT%
