$hudFolder = $PSScriptRoot
$hudShell = New-Object -ComObject WScript.Shell
$hudShortcut = $hudShell.CreateShortcut((Join-Path $hudFolder 'Meeting HUD.lnk'))
$hudShortcut.TargetPath = Join-Path $hudFolder '.venv\Scripts\pythonw.exe'
$hudShortcut.Arguments = '"' + (Join-Path $hudFolder 'launcher.pyw') + '"'
$hudShortcut.WorkingDirectory = $hudFolder
$hudShortcut.WindowStyle = 7
$hudShortcut.Description = 'Meeting HUD - restore with Ctrl+Shift+H'
$hudShortcut.Save()
Write-Host 'Created Meeting HUD.lnk. Double-click it for console-free startup.'
