# Starts Docker when the engine is available, then the API, voice worker, and desk.
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
Set-Location $Root

docker info *> $null
if ($LASTEXITCODE -eq 0) {
    docker compose up -d
    Write-Output "Docker services are starting. Postgres, Redis, LiveKit, Kokoro, Prometheus, Grafana, and Langfuse are in docker-compose.yml."
} else {
    Write-Output "Docker engine is not running. The compose file is ready. On this PC the engine needs an administrator to install WSL2 or grant Hyper-V access, then restart Docker Desktop."
}

Start-Process -FilePath $Python -ArgumentList "-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8000" -WorkingDirectory (Join-Path $Root "backend")
Start-Process -FilePath $Python -ArgumentList "-m","app.workers.livekit_worker","dev" -WorkingDirectory (Join-Path $Root "backend")
Start-Process -FilePath "npm" -ArgumentList "run","dev" -WorkingDirectory (Join-Path $Root "frontend")
Write-Output "API http://127.0.0.1:8000/api/health"
Write-Output "Desk http://127.0.0.1:5173"
