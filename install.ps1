# gradgate on windows: a virtual environment, the gradgate command and a .env with the free public endpoints.
# run it from powershell in the repository folder:   .\install.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = if ($env:PYTHON) { $env:PYTHON } else { "python" }
$ok = & $py -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"; if ($LASTEXITCODE -ne 0) {
  Write-Host "gradgate needs python 3.11 or newer ($(& $py --version))"; exit 1
}
if (-not (Test-Path .venv)) { & $py -m venv .venv }
& .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
& .venv\Scripts\python.exe -m pip install --quiet -e .
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
Write-Host ""
Write-Host "  gradgate_ is installed."
Write-Host ""
Write-Host "  .venv\Scripts\gradgate doctor   check the rpc, websocket and settings"
Write-Host "  .venv\Scripts\gradgate start    the engine and the terminal on http://127.0.0.1:8765"
Write-Host ""
