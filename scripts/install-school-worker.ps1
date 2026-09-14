param(
    [string]$InstallDir = "C:\RFSimWorker",
    [string]$Python = "py",
    [string]$HostUrl = "https://HOST-ADRESI",
    [string]$WorkerId = $env:COMPUTERNAME,
    [string]$CstRoot = "C:\Program Files\CST Studio Suite 2025",
    [switch]$WithCstDependencies
)
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$target = [System.IO.Path]::GetFullPath($InstallDir)
New-Item -ItemType Directory -Force -Path $target | Out-Null
$venv = Join-Path $target "venv"
if ($Python -eq "py") { & py -3.11 -m venv $venv } else { & $Python -m venv $venv }
$venvPython = Join-Path $venv "Scripts\python.exe"
if ($WithCstDependencies) {
    & $venvPython -m pip install --disable-pip-version-check -e "$repo[cst]"
} else {
    & $venvPython -m pip install --disable-pip-version-check -e $repo
}
$config = Join-Path $target "worker.toml"
$dataDir = (Join-Path $target "data").Replace("\", "/")
$escapedCst = $CstRoot.Replace("\", "/")
$configText = @"
[worker]
host_url = "$HostUrl"
worker_id = "$WorkerId"
data_dir = "$dataDir"
heartbeat_seconds = 30
poll_seconds = 15
min_free_gb = 15
cleanup_after_upload = false
cst_roots = ["$escapedCst"]

[runners.mock-v1]
type = "mock"
max_mock_delay_seconds = 5
"@
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($config, $configText, $utf8NoBom)
$launcher = Join-Path $target "RF-Sim-Okul-Istemcisi.cmd"
@"
@echo off
"$venvPython" -m rfsim worker-gui --config "$config"
pause
"@ | Set-Content -LiteralPath $launcher -Encoding ascii
Write-Host "Install ready: $launcher"
Write-Host "Cleanup is disabled. Set RF_SIM_TOKEN, then open the GUI."
