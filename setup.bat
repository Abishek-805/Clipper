@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    echo Python 3.11 or newer is recommended. Install it from https://www.python.org/downloads/windows/ and retry.
    pause
    exit /b 1
)
py -3.14 --version >nul 2>nul
if errorlevel 1 py -3.13 --version >nul 2>nul
if errorlevel 1 py -3.12 --version >nul 2>nul
if errorlevel 1 py -3.11 --version >nul 2>nul
if errorlevel 1 (
    echo Python 3.11 or newer was not found. Install one, then run setup.bat again.
    pause
    exit /b 1
)
if not exist .venv\Scripts\python.exe (
    py -3.14 -m venv .venv 2>nul || py -3.13 -m venv .venv 2>nul || py -3.12 -m venv .venv 2>nul || py -3.11 -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo Dependency installation failed. Check the pip output above.
    pause
    exit /b 1
)
where ffmpeg >nul 2>nul
if errorlevel 1 echo FFmpeg was not found. Install a Windows FFmpeg build and add its bin folder to PATH.
where ffprobe >nul 2>nul
if errorlevel 1 echo FFprobe was not found. It is normally included with FFmpeg.
python -c "import tkinter; print('Tkinter: available')" 2>nul || echo Tkinter is missing from this Python installation.
python -c "import faster_whisper; print('Faster-Whisper: available; the small model downloads on first run')" 2>nul
echo Setup finished. Run run.bat to launch the application.
pause
