param([string]$InstallDir='C:\RFSimWorker',
      [ValidatePattern('^[A-Za-z0-9_-]+$')][string]$RecoverJob='school-g5m5-11-g5-m01-nominal-left-e16-s018')
$ErrorActionPreference='Stop'
$repo=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$target=(Resolve-Path -LiteralPath $InstallDir).Path
$python=Join-Path $target 'venv\Scripts\python.exe'
$config=Join-Path $target 'worker.toml'
$token=Join-Path $target 'worker-token.dpapi'
foreach($file in @($python,$config,$token)) {
    if(-not(Test-Path -LiteralPath $file -PathType Leaf)){throw "Missing: $file"}
}
# Migration only: do not stop an existing worker while CST is open.
$cst=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -match 'Solver_HF|CST DESIGN ENVIRONMENT'})
if($cst.Count){throw 'CST is open. Existing simulation preserved; supervisor not installed yet.'}
$pattern='(?i)-m\s+rfsim\s+(worker|worker-gui)\s'
$old=@(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w|3.*)?\.exe$' -and $_.CommandLine -match $pattern -and
    $_.CommandLine.Contains($config)
})
foreach($process in $old){Stop-Process -Id $process.ProcessId -ErrorAction SilentlyContinue}
$workerText=Get-Content -LiteralPath $config -Raw
$match=[regex]::Match($workerText,'(?m)^\s*worker_id\s*=\s*"([^"]+)"')
if(-not $match.Success){throw 'worker_id missing'}
$workerId=$match.Groups[1].Value
$bootstrap=Join-Path $target 'start-school-supervisor.ps1'
$log=Join-Path $target 'supervisor.log'
$body=@"
`$ErrorActionPreference='Stop'
`$mutex=New-Object Threading.Mutex(`$false, 'Local\RFSimWorker-$workerId')
if(-not `$mutex.WaitOne(0)){Write-Host 'An existing worker still owns the lock; no duplicate started.';exit 23}
try {
    `$secure=(Get-Content -LiteralPath '$token' -Raw).Trim() | ConvertTo-SecureString
    `$ptr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR(`$secure)
    try {`$env:RF_SIM_TOKEN=[Runtime.InteropServices.Marshal]::PtrToStringBSTR(`$ptr)}
    finally {[Runtime.InteropServices.Marshal]::ZeroFreeBSTR(`$ptr)}
    `$env:PYTHONUTF8='1'
    while(-not(Test-Path -LiteralPath '$target\STOP-SUPERVISOR')) {
        `$script='$repo\scripts\school-supervisor.py'
        `$statusFile='$target\supervisor-status.json'
        if(Test-Path -LiteralPath `$statusFile) {
            `$status=Get-Content -LiteralPath `$statusFile -Raw | ConvertFrom-Json
            if(`$status.active_release -and (Test-Path -LiteralPath (Join-Path `$status.active_release '.verified-release'))) {
                `$script=Join-Path `$status.active_release 'scripts\school-supervisor.py'
            }
        }
        try {
            `$step=Start-Process -FilePath '$python' -WindowStyle Hidden -Wait -PassThru -ArgumentList @(
                ('"'+`$script+'"'),'--repo','"$repo"','--config','"$config"',
                '--recover-job','$RecoverJob','--one-cycle') -RedirectStandardOutput '$target\supervisor-step.stdout.log' -RedirectStandardError '$target\supervisor-step.stderr.log'
            if(`$step.ExitCode -ne 0){Add-Content -LiteralPath '$log' -Value ('Supervisor step exited: '+`$step.ExitCode)}
        } catch {Add-Content -LiteralPath '$log' -Value (`$_ | Out-String)}
        Start-Sleep -Seconds 15
    }
} finally {`$mutex.ReleaseMutex();`$mutex.Dispose()}
"@
[IO.File]::WriteAllText($bootstrap,$body,(New-Object Text.UTF8Encoding($false)))
$startup=Join-Path ([Environment]::GetFolderPath('Startup')) 'RF-Sim-Okul-Isci.cmd'
[IO.File]::WriteAllText($startup,"@echo off`r`npowershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$bootstrap`"`r`n",[Text.Encoding]::ASCII)
# The old worker child has exited; allow its PowerShell finally to release mutex.
Start-Sleep -Seconds 2
$p=Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$bootstrap`"") -PassThru
Start-Sleep -Seconds 3
$p.Refresh()
if($p.HasExited){throw "Supervisor exited with code $($p.ExitCode). See $log"}
Write-Host "Supervisor started: PID $($p.Id). Updates run between jobs; models remain pinned."
Write-Host "Status: $target\supervisor-status.json"
