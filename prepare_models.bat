@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run setup.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" prepare_models.py
set "HUD_MODELS_EXIT=%errorlevel%"
pause
exit /b %HUD_MODELS_EXIT%
