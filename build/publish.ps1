# Publica o exe compilado no branch `releases` (so exe + SHA256SUMS + version.json).
# Uso: .\build\publish.ps1 -Version 1.0.1   (depois de .\build\build.ps1)
param([Parameter(Mandatory=$true)][string]$Version)
# git fala no stderr; no PowerShell 5.1 isso viraria erro com Stop.
$ErrorActionPreference = "Continue"
function Run([string]$what, [scriptblock]$cmd) { & $cmd; if ($LASTEXITCODE -ne 0) { throw "$what falhou (exit $LASTEXITCODE)" } }
$root = Split-Path $PSScriptRoot -Parent
$exe = Join-Path $root "dist\RapidPrintAgent.exe"
if (-not (Test-Path $exe)) { throw "dist\RapidPrintAgent.exe nao existe; rode build\build.ps1 primeiro" }
$sha = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
$wt = Join-Path $env:TEMP "rpa-releases"
if (Test-Path $wt) { git -C $root worktree remove --force $wt }
Run "fetch" { git -C $root fetch origin releases }
Run "worktree" { git -C $root worktree add $wt releases }
Copy-Item $exe (Join-Path $wt "RapidPrintAgent.exe") -Force
"$sha  RapidPrintAgent.exe" | Out-File (Join-Path $wt "SHA256SUMS") -Encoding ascii
"{`"version`": `"$Version`", `"file`": `"RapidPrintAgent.exe`", `"sha256`": `"$sha`", `"built`": `"$(Get-Date -Format yyyy-MM-dd)`"}" | Out-File (Join-Path $wt "version.json") -Encoding ascii
Run "add" { git -C $wt add -A }
Run "commit" { git -C $wt commit -m "release $Version" }
Run "push" { git -C $wt push origin releases }
git -C $root worktree remove --force $wt
Write-Host "Publicado $Version  sha256=$sha"
Write-Host "No TMS (AWS): PRINT_AGENT_LATEST_VERSION=$Version  PRINT_AGENT_SHA256=$sha"
