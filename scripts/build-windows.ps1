param(
    [string]$PyInstallerVersion = "6.22.3"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot

Push-Location $RepoRoot
try {
    uv run --with "pyinstaller==$PyInstallerVersion" `
        python scripts/build-binary.py
    if ($LASTEXITCODE -ne 0) { throw "Build failed with exit code $LASTEXITCODE." }
}
finally {
    Pop-Location
}
