param(
    [string]$InstallDir = 'C:\RFSimWorker',
    [string]$JobId = 'school-g5-bridge-control-v4-20260914'
)
$ErrorActionPreference = 'Stop'
if ($JobId -notmatch '^[a-zA-Z0-9_-]+$') { throw 'Invalid job ID' }
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $InstallDir 'venv\Scripts\python.exe'
$runDir = Join-Path $InstallDir "data\runs\$JobId"
if (-not (Test-Path -LiteralPath (Join-Path $runDir 'job.json'))) { throw 'Run evidence not found' }
& $python -m pip install --disable-pip-version-check -e "$repo[cst]"
if ($LASTEXITCODE -ne 0) { throw 'Update failed' }
& $python -m rfsim.cst_abort_guard --run-dir $runDir
if ($LASTEXITCODE -ne 0) { throw 'Recovery command failed; do not restart the worker' }
foreach ($relative in @('logs\abort-dialog.json', 'logs\abort-dialog-error.json', 'source\cst-archive\timing.json')) {
    $evidence = Join-Path $runDir $relative
    if (Test-Path -LiteralPath $evidence) { Get-Content -LiteralPath $evidence -Raw }
}
Get-CimInstance Win32_Process | Where-Object {
    $_.Name -like 'Solver_HF_*' -or $_.Name -like 'CST DESIGN ENVIRONMENT*'
} | Select-Object ProcessId, ParentProcessId, CreationDate, Name, CommandLine | Format-List
Write-Output 'Recovery evidence collected. No worker or new solver was started. Keep this output for review.'
