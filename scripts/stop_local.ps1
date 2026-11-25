# Stops the local API and Vite dev server by port. Does not stop Docker.
foreach ($port in 8000, 5173) {
    $lines = netstat -ano | Select-String ":$port\s"
    foreach ($line in $lines) {
        $processId = ($line.ToString() -split "\s+")[-1]
        if ($processId -match "^\d+$" -and $processId -ne "0") {
            Stop-Process -Id ([int]$processId) -Force -ErrorAction SilentlyContinue
        }
    }
}
Write-Output "Stopped listeners on ports 8000 and 5173."
