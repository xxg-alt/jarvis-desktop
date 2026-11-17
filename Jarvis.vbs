Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = folder
pythonw = folder & "\.venv\Scripts\pythonw.exe"
script = folder & "\jarvis_gui.py"
q = Chr(34)
shell.Run q & pythonw & q & " " & q & script & q, 0, False
