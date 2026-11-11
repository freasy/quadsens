# Build a single-file QuadSens executable into dist\quadsens.exe (Windows).
#
#   powershell -ExecutionPolicy Bypass -File build-windows.ps1
#
# Needs Python 3.10 or newer on PATH (python.org installer or the "py" launcher).
# Everything else is fetched from PyPI into .\.venv; nothing is installed
# system-wide. The icon and the installer script are bundled as data, as in
# build.sh. The Windows build has no virtual mouse and no driver installer;
# those modules are bundled but never imported there.
#
# NOT YET RUN ON REAL WINDOWS: written on Linux from the PyInstaller
# documentation. If a step fails, the command line of that step is the place
# to look.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$python = "python"
if (-not (Get-Command $python -ErrorAction SilentlyContinue)) { $python = "py" }

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & $python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "could not create the virtual environment" }
}

& .venv\Scripts\python.exe -m pip install --quiet --disable-pip-version-check "PySide6-Essentials>=6.5" pyinstaller
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

& .venv\Scripts\pyinstaller.exe --noconfirm --clean --onefile --windowed --name quadsens `
    --paths . --exclude-module PyQt6 --exclude-module tkinter `
    --icon assets\quadsens.ico `
    --add-data "assets/quadsens.svg:assets" --hidden-import PySide6.QtSvg `
    packaging\entry.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Write-Host "Built: $PSScriptRoot\dist\quadsens.exe"
