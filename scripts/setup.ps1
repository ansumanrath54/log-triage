# One-time setup for Windows (PowerShell). Run from the project root:
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "==> Creating Python virtual environment (.venv)"
python -m venv .venv

Write-Host "==> Installing backend dependencies"
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt

Write-Host "==> Installing frontend packages"
Push-Location frontend
npm install
Pop-Location

Write-Host ""
Write-Host "Done. Next: open this folder in VS Code and follow the 'Open in VS Code' section of README.md"
