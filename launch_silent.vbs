' Lanza Smart Recorder sin ventana de consola.
' Es "dinámico": calcula su propia carpeta, así que el acceso directo
' funciona igual si este proyecto se mueve o se clona en otro ordenador.
'
' Busca el intérprete de Python en este orden:
'   1) .venv\Scripts\pythonw.exe   (entorno virtual dentro del propio proyecto)
'   2) C:\venvs\smartrecorder\Scripts\pythonw.exe  (entorno alternativo, ruta corta)
'   3) pythonw.exe del PATH del sistema

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

projectDir = fso.GetParentFolderName(WScript.ScriptFullName)
mainPy = """" & projectDir & "\main.py" & """"

candidates = Array( _
    projectDir & "\.venv\Scripts\pythonw.exe", _
    "C:\venvs\smartrecorder\Scripts\pythonw.exe" _
)

pythonw = ""
For Each c In candidates
    If fso.FileExists(c) Then
        pythonw = c
        Exit For
    End If
Next

If pythonw = "" Then
    pythonw = "pythonw.exe"  ' último recurso: confiar en el PATH
End If

shell.CurrentDirectory = projectDir
shell.Run """" & pythonw & """ " & mainPy, 0, False
