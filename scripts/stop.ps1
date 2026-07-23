[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$command = Get-Command docker -ErrorAction SilentlyContinue
$docker = if ($command) { $command.Source } else { $null }
if (-not $docker -and (Test-Path -LiteralPath "D:\Docker\DockerDesktop\resources\bin\docker.exe")) {
    $docker = "D:\Docker\DockerDesktop\resources\bin\docker.exe"
}
if (-not $docker) { throw "Docker CLI not found." }
$dockerBin = Split-Path -Parent $docker
if (($env:PATH -split ";") -notcontains $dockerBin) { $env:PATH = "$dockerBin;$env:PATH" }

& $docker compose --project-directory $root down
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "ChestVision stopped. Database and object-storage volumes were preserved."
