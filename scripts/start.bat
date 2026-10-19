@echo off
REM Cinelume launcher (phase 7.4). Double-click or run from a terminal.
setlocal

set ROOT=%~dp0..
set PYTHON=%ROOT%\venv\Scripts\python.exe

if not exist "%PYTHON%" (
    echo Virtual environment not found. Run scripts\setup.ps1 first.
    pause
    exit /b 1
)

echo Starting Cinelume on http://127.0.0.1:8000 ...
start "" http://127.0.0.1:8000

"%PYTHON%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir "%ROOT%\backend"

pause
