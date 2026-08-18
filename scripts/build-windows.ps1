param(
    [string]$NuitkaVersion = "4.1.3"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot

Push-Location $RepoRoot
try {
    uv run --with "nuitka==$NuitkaVersion" --with ordered-set --with zstandard `
        python scripts/build-binary.py
    if ($LASTEXITCODE -ne 0) { throw "Build failed with exit code $LASTEXITCODE." }
}
finally {
    Pop-Location
}
