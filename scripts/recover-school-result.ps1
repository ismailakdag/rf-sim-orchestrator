param([Parameter(Mandatory=$true)][string]$HostUrl,[string]$InstallDir='C:\RFSimWorker',
      [ValidatePattern('^[A-Za-z0-9_-]+$')][string]$JobId='school-g5-bridge-control-v5-20260914')
$ErrorActionPreference='Stop'
$repo=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python=Join-Path $InstallDir 'venv\Scripts\python.exe'
& $python -m pip install --disable-pip-version-check -e "$repo[cst]"
if($LASTEXITCODE -ne 0){throw 'Package update failed'}
$secure=(Get-Content (Join-Path $InstallDir 'worker-token.dpapi') -Raw).Trim() | ConvertTo-SecureString
$ptr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $env:RF_SIM_TOKEN=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
& $python -m rfsim.recover_upload --url $HostUrl --worker-id OKUL-PC-01 --run-dir (Join-Path $InstallDir "data\runs\$JobId")
if($LASTEXITCODE -ne 0){throw 'Result recovery failed; original retained, worker not restarted'}
& (Join-Path $PSScriptRoot 'enable-school-cst2025-pilot.ps1') -InstallDir $InstallDir -HostUrl $HostUrl
