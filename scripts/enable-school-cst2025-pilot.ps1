param(
    [string]$InstallDir = "C:\RFSimWorker",
    [string]$CstExecutable = "C:\Program Files (x86)\CST Studio Suite 2025\CST DESIGN ENVIRONMENT.exe",
    [string]$HostUrl = "",
    [string]$WorkerId = ""
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$target = [System.IO.Path]::GetFullPath($InstallDir)
$config = Join-Path $target "worker.toml"
$venvPython = Join-Path $target "venv\Scripts\python.exe"
$adapter = Join-Path $repo "adapters\candidate_local_metal_v2.py"
$source = Join-Path $repo "pilot\school-widefield-cst2025-v1\source"
$manifestPath = Join-Path $source "source-manifest.json"
$caseTemplate = Join-Path $source "pilot-case.json"

foreach ($required in @($CstExecutable, $venvPython, $adapter, $manifestPath, $caseTemplate)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Gerekli dosya bulunamadı: $required" }
}
$cstRoot = Split-Path -Parent (Resolve-Path -LiteralPath $CstExecutable).Path
$cstLibraries = Join-Path $cstRoot "AMD64\python_cst_libraries"
if (-not (Test-Path -LiteralPath $cstLibraries -PathType Container)) {
    throw "CST 2025 Python API dizini bulunamadı: $cstLibraries"
}

if (Test-Path -LiteralPath $config -PathType Leaf) {
    $oldText = Get-Content -LiteralPath $config -Raw
    if (-not $HostUrl) {
        $match = [regex]::Match($oldText, '(?m)^\s*host_url\s*=\s*"([^"]+)"')
        if ($match.Success) { $HostUrl = $match.Groups[1].Value }
    }
    if (-not $WorkerId) {
        $match = [regex]::Match($oldText, '(?m)^\s*worker_id\s*=\s*"([^"]+)"')
        if ($match.Success) { $WorkerId = $match.Groups[1].Value }
    }
}
if (-not $HostUrl) { throw "Host URL mevcut worker.toml içinde bulunamadı; -HostUrl verin." }
if (-not $WorkerId) { $WorkerId = $env:COMPUTERNAME }

& $venvPython -m pip install --disable-pip-version-check -e "$repo[cst]"
if ($LASTEXITCODE -ne 0) { throw "CST bağımlılıklarının kurulumu başarısız oldu." }

$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if (-not $manifest.sha256) { throw "Kaynak manifestinde sha256 haritası yok." }
$sourceResolved = (Resolve-Path -LiteralPath $source).Path
foreach ($property in $manifest.sha256.PSObject.Properties) {
    $entry = Join-Path $sourceResolved $property.Name
    $entryResolved = (Resolve-Path -LiteralPath $entry).Path
    if (-not $entryResolved.StartsWith($sourceResolved + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Kaynak manifest yolu kökten çıkıyor: $($property.Name)"
    }
    $observed = (Get-FileHash -Algorithm SHA256 -LiteralPath $entryResolved).Hash.ToLowerInvariant()
    if ($observed -ne $property.Value) { throw "Kaynak karması uyuşmuyor: $($property.Name)" }
}

$case = Get-Content -LiteralPath $caseTemplate -Raw | ConvertFrom-Json
$caseId = [string]$case.case_id
if (-not $caseId) { throw "Pilot vaka kimliği boş." }
$adapterHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $adapter).Hash.ToLowerInvariant()
$sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $manifestPath).Hash.ToLowerInvariant()

function TomlValue([string]$value) {
    return $value.Replace('\', '\\').Replace('"', '\"')
}
function SlashPath([string]$value) {
    return (Resolve-Path -LiteralPath $value).Path.Replace('\', '/')
}

New-Item -ItemType Directory -Force -Path $target | Out-Null
if (Test-Path -LiteralPath $config -PathType Leaf) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    Copy-Item -LiteralPath $config -Destination "$config.$stamp.bak"
}
$dataDir = (Join-Path $target "data").Replace('\', '/')
$pythonToml = SlashPath $venvPython
$adapterToml = SlashPath $adapter
$sourceToml = SlashPath $source
$cstRootToml = SlashPath $cstRoot
$cstLibrariesToml = SlashPath $cstLibraries
$configText = @"
[worker]
host_url = "$(TomlValue $HostUrl)"
worker_id = "$(TomlValue $WorkerId)"
data_dir = "$(TomlValue $dataDir)"
heartbeat_seconds = 30
poll_seconds = 15
min_free_gb = 15
cleanup_after_upload = false
cst_roots = ["$(TomlValue $cstRootToml)"]

[runners.mock-v1]
type = "mock"
max_mock_delay_seconds = 5

[runners.cst-widefield-cst2025-pilot-v1]
type = "fixed_python"
python = "$(TomlValue $pythonToml)"
script = "$(TomlValue $adapterToml)"
script_sha256 = "$adapterHash"
source_sha256 = "$sourceHash"
cst_python_libraries = "$(TomlValue $cstLibrariesToml)"
expected_cst_major = 2025
timeout_seconds = 1200
arguments = ["{job_file}", "{run_dir}", "--source-root", "$(TomlValue $sourceToml)", "--case-template", "pilot-case.json"]

[runners.cst-widefield-cst2025-pilot-v1.parameter_schema.case_id]
type = "string"
required = true
enum = ["$(TomlValue $caseId)"]
"@
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($config, $configText, $utf8NoBom)

$launcher = Join-Path $target "RF-Sim-Okul-Istemcisi.cmd"
@"
@echo off
"$venvPython" -m rfsim worker-gui --config "$config"
pause
"@ | Set-Content -LiteralPath $launcher -Encoding ascii

Write-Host "Pilot yapılandırması hazır. CST ve kaynak denetimi:" -ForegroundColor Green
& $venvPython -m rfsim probe --config $config
if ($LASTEXITCODE -ne 0) { throw "Yerel probe başarısız oldu." }
Write-Host "Temizlik kapalıdır. GUI: $launcher" -ForegroundColor Green
