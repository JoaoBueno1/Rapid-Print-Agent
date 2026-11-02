# Builds dist\RapidPrintAgent.exe (one file, windowed) and prints its SHA-256.
# Usage:  powershell -ExecutionPolicy Bypass -File build\build.ps1
# Needs:  python 3.12 x64 with requirements.txt + pyinstaller installed.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$version = (python -c "import rapid_print_agent as a; print(a.__version__)").Trim()
Write-Host "Building Rapid Print Agent v$version"

# Version resource (shows in file Properties > Details).
$parts = ($version.Split(".") + @("0","0","0"))[0..3] -join ", "
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($parts), prodvers=($parts), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Rapid Express'),
      StringStruct('FileDescription', 'Rapid Print Agent'),
      StringStruct('FileVersion', '$version'),
      StringStruct('InternalName', 'RapidPrintAgent'),
      StringStruct('OriginalFilename', 'RapidPrintAgent.exe'),
      StringStruct('ProductName', 'Rapid Print Agent'),
      StringStruct('ProductVersion', '$version')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@ | Set-Content -Encoding ASCII (Join-Path $root "build\version_info.txt")

python -m PyInstaller --noconfirm --clean --distpath dist --workpath build\work build\RapidPrintAgent.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$exe = Join-Path $root "dist\RapidPrintAgent.exe"
$hash = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
$size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host ""
Write-Host "dist\RapidPrintAgent.exe  $size MB"
Write-Host "SHA256  $hash"
Set-Content -Encoding ASCII (Join-Path $root "dist\RapidPrintAgent.exe.sha256") "$hash  RapidPrintAgent.exe"
Write-Host ""
Write-Host "Server env for auto-update:"
Write-Host "  PRINT_AGENT_LATEST_VERSION=$version"
Write-Host "  PRINT_AGENT_SHA256=$hash"
Write-Host "  PRINT_AGENT_DOWNLOAD_URL=<direct .exe URL of this release>"
