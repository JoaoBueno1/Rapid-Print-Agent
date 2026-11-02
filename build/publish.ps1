# Publica o exe compilado no branch `releases` (so exe + SHA256SUMS + version.json).
# Uso: .\build\publish.ps1 -Version 1.0.1   (depois de .\build\build.ps1)
param([Parameter(Mandatory=$true)][string]$Version)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$exe = Join-Path $root "dist\RapidPrintAgent.exe"
if (-not (Test-Path $exe)) { throw "dist\RapidPrintAgent.exe nao existe; rode build\build.ps1 primeiro" }
$sha = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
$wt = Join-Path $env:TEMP "rpa-releases"
if (Test-Path $wt) { git -C $root worktree remove --force $wt }
git -C $root fetch origin releases 2>$null
git -C $root worktree add $wt releases
Copy-Item $exe (Join-Path $wt "RapidPrintAgent.exe") -Force
"$sha  RapidPrintAgent.exe" | Out-File (Join-Path $wt "SHA256SUMS") -Encoding ascii
"{`"version`": `"$Version`", `"file`": `"RapidPrintAgent.exe`", `"sha256`": `"$sha`", `"built`": `"$(Get-Date -Format yyyy-MM-dd)`"}" | Out-File (Join-Path $wt "version.json") -Encoding ascii
git -C $wt add -A
git -C $wt commit -m "release $Version"
git -C $wt push origin releases
git -C $root worktree remove --force $wt
Write-Host "Publicado $Version  sha256=$sha"
Write-Host "No TMS (AWS): PRINT_AGENT_LATEST_VERSION=$Version  PRINT_AGENT_SHA256=$sha"
