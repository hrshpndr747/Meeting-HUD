Option Explicit
Dim shell, fso, folder, python, launcher
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
python = fso.BuildPath(folder, ".venv\Scripts\pythonw.exe")
launcher = fso.BuildPath(folder, "launcher.pyw")
If Not fso.FileExists(python) Then
    MsgBox "Run setup.bat first. See SETUP.md.", 16, "Meeting HUD"
    WScript.Quit 2
End If
shell.CurrentDirectory = folder
shell.Run Chr(34) & python & Chr(34) & " " & Chr(34) & launcher & Chr(34), 0, False
