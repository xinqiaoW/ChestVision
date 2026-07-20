[CmdletBinding()]
param(
    [switch]$Build,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$runtimeTemp = "D:\Temp"
New-Item -ItemType Directory -Path $runtimeTemp -Force | Out-Null
$env:TEMP = $runtimeTemp
$env:TMP = $runtimeTemp

function Find-DockerCli {
    $command = Get-Command docker -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }

    $candidates = @(
        "D:\Docker\DockerDesktop\resources\bin\docker.exe",
        "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe",
        "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    )
    return $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}

function Find-DockerDesktop {
    $candidates = @(
        "D:\Docker\DockerDesktop\Docker Desktop.exe",
        "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe",
        "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    )
    return $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}

& (Join-Path $PSScriptRoot "setup-env.ps1")
foreach ($directory in @("backend\models", "backend\datasets", "backend\runs")) {
    New-Item -ItemType Directory -Path (Join-Path $root $directory) -Force | Out-Null
}

$docker = Find-DockerCli
if (-not $docker) {
    throw "Docker CLI not found. Install Docker Desktop first (configured location: D:\Docker\DockerDesktop)."
}
$dockerBin = Split-Path -Parent $docker
if (($env:PATH -split ";") -notcontains $dockerBin) {
    $env:PATH = "$dockerBin;$env:PATH"
}

& $docker info *> $null
if ($LASTEXITCODE -ne 0) {
    $desktop = Find-DockerDesktop
    if (-not $desktop) { throw "Docker Desktop is installed but its launcher was not found." }
    Write-Host "[docker] Starting Docker Desktop..."
    Start-Process -FilePath $desktop -WindowStyle Hidden
}

$deadline = (Get-Date).AddMinutes(4)
$engineReady = $false
while ((Get-Date) -lt $deadline) {
    & $docker info *> $null
    if ($LASTEXITCODE -eq 0) {
        $engineReady = $true
        break
    }
    Write-Host "[docker] Waiting for Docker engine..."
    Start-Sleep -Seconds 3
}
if (-not $engineReady) { throw "Docker engine did not become ready within 4 minutes." }

if (-not (Test-Path -LiteralPath (Join-Path $root "backend\models\best.pt"))) {
    Write-Warning "backend/models/best.pt is missing. The UI will start, but detection will be unavailable."
}

$arguments = @("compose", "--project-directory", $root, "up", "-d")
if ($Build) { $arguments += "--build" }
Write-Host "[compose] Starting ChestVision (missing images are built automatically)..."
& $docker @arguments
if ($LASTEXITCODE -ne 0) { throw "docker compose up failed." }

$healthUrl = "http://localhost:8000/api/health/detail"
$ready = $false
for ($attempt = 1; $attempt -le 60; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 5
        if ($health.data.status -in @("healthy", "degraded")) {
            $ready = $true
            break
        }
    } catch {}
    Start-Sleep -Seconds 2
}
if (-not $ready) {
    & $docker compose --project-directory $root ps
    throw "ChestVision API did not become ready. Run scripts\logs.ps1 for details."
}

Write-Host ""
Write-Host "ChestVision is ready:" -ForegroundColor Green
Write-Host "  Web:       http://localhost:5173"
Write-Host "  API docs:  http://localhost:8000/docs"
Write-Host "  MinIO:     http://localhost:9001"
Write-Host "  Login:     admin / admin123"

if (-not $NoBrowser) {
    Start-Process "http://localhost:5173"
}
