param(
    [string]$OutputDir = "build/nuitka"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot

Push-Location $RepoRoot
try {
    if (-not (Test-Path "tracesurface/server/static/index.html")) {
        throw "tracesurface/server/static/index.html not found. Run npm run build in frontend first."
    }

    Remove-Item $OutputDir -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

    uv run --with nuitka --with ordered-set --with zstandard python -m nuitka `
        --mode=standalone `
        --python-flag=-m `
        --msvc=latest `
        --assume-yes-for-downloads `
        --enable-plugin=playwright `
        --playwright-include-browser=none `
        --output-dir=$OutputDir `
        --output-filename=tracesurface `
        --include-data-dir=tracesurface/server/static=tracesurface/server/static `
        --include-data-dir=tracesurface/storage/sqlite/migrations=tracesurface/storage/sqlite/migrations `
        --include-data-files=tracesurface/secrets/rules.yml=tracesurface/secrets/rules.yml `
        tracesurface

    if ($LASTEXITCODE -ne 0) {
        throw "Nuitka failed with exit code $LASTEXITCODE."
    }

    $Exe = Get-ChildItem $OutputDir -Recurse -Filter "tracesurface.exe" | Select-Object -First 1
    if (-not $Exe) {
        throw "Nuitka build finished, but tracesurface.exe was not found."
    }

    Write-Host "Built $($Exe.FullName)"
}
finally {
    Pop-Location
}
