@echo off
rem gradgate for windows: double-click. The first run installs (a minute), every run starts the engine and opens the terminal.
cd /d "%~dp0"
if not exist ".venv\Scripts\gradgate.exe" (
  echo installing gradgate ^(first run only^)...
  powershell -ExecutionPolicy Bypass -File "%~dp0install.ps1" || (echo. & echo install failed - python 3.11+ is needed: https://www.python.org/downloads/ & pause & exit /b 1)
)
".venv\Scripts\gradgate.exe" start
pause
