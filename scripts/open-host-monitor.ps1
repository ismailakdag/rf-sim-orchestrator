param(
    [string]$HostUrl = 'http://127.0.0.1:8876',
    [string]$TokenFile = '',
    [string]$LocalCurrent = ''
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if ($TokenFile) { $env:RF_SIM_TOKEN = [IO.File]::ReadAllText($TokenFile).Trim() }
if (-not $env:RF_SIM_TOKEN) { throw 'RF_SIM_TOKEN or -TokenFile is required.' }
$python = Join-Path $repo '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Local .venv Python is missing.' }
$env:PYTHONIOENCODING = 'utf-8'
$argsList = @('-m', 'rfsim', 'monitor-gui', '--url', $HostUrl)
if ($LocalCurrent) { $argsList += @('--local-current', $LocalCurrent) }
# No secrets appear in command-line arguments. The GUI has no solver controls.
& $python @argsList
