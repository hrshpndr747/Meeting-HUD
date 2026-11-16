@echo off
setlocal
cd /d "%~dp0"
".venv\Scripts\python.exe" main.py %*
set "hud_result=%ERRORLEVEL%"
if not "%hud_result%"=="0" (
    echo Meeting HUD exited with code %hud_result%. Details are in the logs folder.
    pause
)
exit /b %hud_result%
