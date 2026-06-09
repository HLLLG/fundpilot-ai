$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$ApiDir = Join-Path $Root "apps\api"
$WebDir = Join-Path $Root "apps\web"
$ApiPython = Join-Path $ApiDir ".venv\Scripts\python.exe"

if (-not (Test-Path $ApiPython)) {
  Write-Host "Creating backend virtual environment..."
  python -m venv (Join-Path $ApiDir ".venv")
}

Write-Host "Starting FundPilot AI services..."

Start-Process powershell -WindowStyle Hidden -ArgumentList @(
  "-NoExit",
  "-Command",
  "cd '$ApiDir'; .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000"
)

Start-Process powershell -WindowStyle Hidden -ArgumentList @(
  "-NoExit",
  "-Command",
  "cd '$WebDir'; npm run dev"
)

Write-Host "API: http://127.0.0.1:8000"
Write-Host "Web: http://127.0.0.1:3001"
Write-Host "Hidden server windows were started. Stop them from Task Manager or by closing their PowerShell processes."
