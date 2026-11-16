@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3.11 -m venv .venv
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install --only-binary=:all: -r requirements.txt -r requirements-gpu.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip check
if errorlevel 1 goto failed
powershell.exe -NoProfile -Command "$p=(Get-Location).Path; $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $p 'Meeting HUD.lnk')); $s.TargetPath=Join-Path $p '.venv\Scripts\pythonw.exe'; $s.Arguments=[string][char]34+(Join-Path $p 'launcher.pyw')+[char]34; $s.WorkingDirectory=$p; $s.WindowStyle=7; $s.Save()"
if errorlevel 1 echo Shortcut creation failed. Use Launch_HUD.vbs instead.
echo Install Ollama and run: ollama pull qwen3:4b-instruct
echo Then double-click Meeting HUD.lnk or Launch_HUD.vbs. See SETUP.md.
pause
exit /b 0
:failed
echo Setup failed. Read the error above. Python 3.11 x64 and internet access are required.
pause
exit /b 1
