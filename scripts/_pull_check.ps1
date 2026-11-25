Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match 'docker|com.docker' -or $_.CommandLine -match 'compose|kokoro' } |
  Select-Object ProcessId, ParentProcessId, Name |
  Format-Table -AutoSize
