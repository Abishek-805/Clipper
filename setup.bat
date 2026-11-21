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
if not exist ".venv\Scripts\python.exe" (
    echo Could not create the project Python environment.
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat || exit /b 1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo Dependency installation failed. Check the pip output above.
    pause
    exit /b 1
)
python -c "import tkinter; print('Tkinter: available')" 2>nul || echo Tkinter is missing from this Python installation.
echo Optional caption setup: download the free Whisper small model once.
choice /c YN /n /m "Download Whisper now? [Y/N] "
if not errorlevel 2 (
    python browser_preview.py --setup-whisper
    if errorlevel 1 echo Whisper setup failed. You can retry later or create clips with captions set to None.
)
python -c "import ctranslate2; print('CTranslate2 CUDA devices:', ctranslate2.get_cuda_device_count())" 2>nul || echo CTranslate2 GPU check failed; processing will use CPU fallback.
python diagnostics.py --check-export
if errorlevel 1 (
    echo Fix the missing requirements above, then run setup.bat again.
    pause
    exit /b 1
)
echo Setup finished. Run run.bat to launch the application.
pause
