@echo off
REM Run NetPulse with tray icon
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found. Running setup...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)
if not exist "config.yaml" (
    echo config.yaml not found. Copy config.example.yaml to config.yaml and edit it first.
    pause
    exit /b 1
)
python tray.py
