param([string]$InstallDir='C:\RFSimWorker',
      [string]$JobId='school-g5m5-11-g5-m01-nominal-left-e16-s018')
$ErrorActionPreference='Stop'
if ($JobId -notmatch '^[A-Za-z0-9_-]+$') { throw 'Invalid job id' }
$run=Join-Path $InstallDir "data\runs\$JobId"
foreach($relative in @('source\cst-archive\record.json','model\cst-work\heartbeat.json')) {
    $file=Join-Path $run $relative
    if(Test-Path -LiteralPath $file) {
        $data=Get-Content -LiteralPath $file -Raw -Encoding UTF8 | ConvertFrom-Json
        $data | Select-Object status,state,last_successful_stage,failure_stage,failure_reason,solver_started,project_closed,elapsed_seconds,utc | Format-List
    } else { Write-Host "Missing: $file" }
}
Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'Solver_HF|CST DESIGN' } |
    Select-Object ProcessId,ParentProcessId,Name,CreationDate | Format-Table
Write-Host 'Read-only diagnostic. No process was stopped or started.'
