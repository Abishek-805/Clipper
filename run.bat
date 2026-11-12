@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
    echo Setup is required. Run setup.bat first.
    pause
    exit /b 1
)
start "AI Video Clipping Factory" .venv\Scripts\pythonw.exe main.py
