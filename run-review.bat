@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Run setup.bat first to create the project virtual environment.
    pause
    exit /b 1
)
.venv\Scripts\python.exe -m review_workspace
if errorlevel 1 pause
