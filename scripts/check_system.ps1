# Checks the local clinic services. Run from sunrise-family-clinic:
#   powershell -ExecutionPolicy Bypass -File .\scripts\check_system.ps1
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
$failed = 0

function Report($name, $ok, $detail) {
    if ($ok) {
        Write-Host "PASS  $name  $detail"
    } else {
        Write-Host "FAIL  $name  $detail"
        $script:failed++
    }
}

try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 5
    Report "API" ($health.status -eq "ok") "database=$($health.database) office_open=$($health.office_open)"
    Report "Ollama" ($health.voice.ollama -eq "up") "model=$($health.voice.ollama_model)"
    Report "LiveKit" ($health.voice.livekit -eq "up") $health.voice.livekit
    Report "Kokoro" ($health.voice.kokoro -eq "up") $health.voice.kokoro
} catch {
    Report "API" $false $_.Exception.Message
}

try {
    $tags = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 5
    $names = @($tags.models | ForEach-Object { $_.name })
    Report "qwen2.5:3b" ($names -contains "qwen2.5:3b") ($names -join ", ")
} catch {
    Report "qwen2.5:3b" $false $_.Exception.Message
}

try {
    $overview = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/overview" -TimeoutSec 5
    $raw = $overview | ConvertTo-Json -Depth 6
    $names = @($overview.patients | ForEach-Object { $_.name })
    $hasPatients = ($names -contains "Sarah Johnson") -and ($names -contains "Michael Brown") -and ($names -contains "Maria Garcia")
    $leaks = $raw -match "3344|1020|9081|1985-03-14|ssn"
    Report "Chart patients" $hasPatients ($names -join ", ")
    Report "Chart hides SSN and DOB" (-not $leaks) $(if ($leaks) { "sensitive value appeared" } else { "hidden" })
} catch {
    Report "Chart" $false $_.Exception.Message
}

Push-Location $Root
$ps = docker compose ps --format "{{.Name}} {{.Status}}" 2>&1
Pop-Location
$wanted = @(
    "postgres",
    "redis",
    "livekit",
    "kokoro",
    "langfuse-web"
)
foreach ($name in $wanted) {
    $line = $ps | Where-Object { $_ -match $name }
    Report "container $name" ($line -match "Up") ($(if ($line) { $line } else { "not found" }))
}

if ($failed -eq 0) {
    Write-Host "All service checks passed."
    exit 0
}
Write-Host "$failed check(s) failed."
exit 1
