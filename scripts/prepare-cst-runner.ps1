param(
    [Parameter(Mandatory=$true)][string]$SourceRoot,
    [string]$Adapter = ""
)
$ErrorActionPreference = "Stop"
if (-not $Adapter) { $Adapter = Join-Path $PSScriptRoot "..\adapters\candidate_local_metal_v2.py" }
$source = (Resolve-Path -LiteralPath $SourceRoot).Path
$adapterPath = (Resolve-Path -LiteralPath $Adapter).Path
$manifest = Join-Path $source "source-manifest.json"
if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) { throw "source-manifest.json is missing" }
[pscustomobject]@{
    adapter = $adapterPath
    adapter_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $adapterPath).Hash.ToLowerInvariant()
    source_root = $source
    source_manifest_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $manifest).Hash.ToLowerInvariant()
} | ConvertTo-Json
