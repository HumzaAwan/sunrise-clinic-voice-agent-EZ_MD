Get-ChildItem -Path "$env:LOCALAPPDATA\Docker","C:\ProgramData\Docker","C:\ProgramData\DockerDesktop" -Recurse -Include *.vhdx,*.qcow2 -ErrorAction SilentlyContinue |
  Select-Object FullName, Length, LastWriteTime |
  Format-List
