@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo The project virtual environment was not found. Run setup.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" browser_preview.py
