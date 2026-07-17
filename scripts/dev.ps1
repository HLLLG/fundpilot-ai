$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$ApiDir = Join-Path $Root "apps\api"
$WebDir = Join-Path $Root "apps\web"
$ApiPython = Join-Path $ApiDir ".venv\Scripts\python.exe"

function Assert-PortAvailable {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port,
        [Parameter(Mandatory = $true)]
        [string]$ServiceName
    )

    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    if ($listener) {
        throw "$ServiceName port $Port is already in use. Stop the existing process before starting another copy."
    }
}

if (-not (Test-Path $ApiPython)) {
    Write-Host "Creating backend virtual environment..."
    python -m venv (Join-Path $ApiDir ".venv")
}

Assert-PortAvailable -Port 8000 -ServiceName "API"
Assert-PortAvailable -Port 3001 -ServiceName "Web"

Write-Host "Starting FundPilot AI services..."

$ApiArguments = @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000")
if ($env:FUND_AI_DEV_RELOAD -eq "true") {
    $ApiArguments += "--reload"
}
$ApiProcess = Start-Process -FilePath $ApiPython `
    -ArgumentList $ApiArguments `
    -WorkingDirectory $ApiDir `
    -WindowStyle Hidden `
    -PassThru

$WebProcess = Start-Process powershell -WindowStyle Hidden -ArgumentList @(
  "-NoExit",
  "-Command",
  "cd '$WebDir'; npm run dev"
) -PassThru

Write-Host "API: http://127.0.0.1:8000"
Write-Host "Web: http://127.0.0.1:3001"
Write-Host "API PID: $($ApiProcess.Id); Web launcher PID: $($WebProcess.Id)"
Write-Host "Set FUND_AI_DEV_RELOAD=true only while actively editing API code."
