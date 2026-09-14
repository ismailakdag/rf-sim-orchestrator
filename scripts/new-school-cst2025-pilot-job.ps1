param(
    [string]$OutputDir = "",
    [int]$DeadlineMinutes = 30
)

$ErrorActionPreference = "Stop"
if ($DeadlineMinutes -lt 21 -or $DeadlineMinutes -gt 60) { throw "DeadlineMinutes 21 ile 60 arasında olmalıdır." }
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$source = Join-Path $repo "pilot\school-widefield-cst2025-v1\source"
$manifest = Join-Path $source "source-manifest.json"
$caseTemplate = Join-Path $source "pilot-case.json"
if (-not $OutputDir) { $OutputDir = Join-Path $repo "build\remote-pilot" }
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$case = Get-Content -LiteralPath $caseTemplate -Raw | ConvertFrom-Json
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss")
$jobId = "school-cst2025-widefield-$stamp"
$document = [ordered]@{
    schema_version = 1
    job_id = $jobId
    study_id = "tooth-sensor-widefield-cross-version-pilot"
    runner = "cst-widefield-cst2025-pilot-v1"
    source = [ordered]@{
        version = "fr4-widefield-txrx-cst2025-pilot-v1"
        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $manifest).Hash.ToLowerInvariant()
    }
    parameters = [ordered]@{ case_id = [string]$case.case_id }
    deadline_utc = (Get-Date).ToUniversalTime().AddMinutes($DeadlineMinutes).ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
    priority = 100
    metadata = [ordered]@{
        purpose = "single CST 2025 CPU cross-version compatibility pilot"
        baseline = "20260914-fr4-widefield-txrx-v1/b00001"
        gpu = $false
        mesh = 32
        cleanup_after_upload = $false
        compact = $false
    }
}
$path = Join-Path $OutputDir "$jobId.json"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($path, (($document | ConvertTo-Json -Depth 8) + "`n"), $utf8NoBom)
Write-Output $path
