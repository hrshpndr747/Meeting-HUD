@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Run setup.bat first. See SETUP.md.
    pause
    exit /b 2
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0launcher.pyw"
exit /b 0
