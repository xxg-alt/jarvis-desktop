@echo off
title Jarvis Installer
cd /d "%~dp0"
echo ============================================
echo   Jarvis v1 Installer
echo ============================================
echo.
where python >nul 2>nul
if %errorlevel% neq 0 goto NO_PYTHON
python --version
echo.
echo [1/3] Creating virtual environment...
python -m venv .venv
if %errorlevel% neq 0 goto FAIL
call .venv\Scripts\activate.bat
echo [2/3] Installing packages, please wait...
python -m pip install --upgrade pip
pip install -r requirements.txt
if %errorlevel% neq 0 goto FAIL
echo.
echo [3/3] Done!
echo Next: double-click Jarvis.vbs to run Jarvis with NO black window (floating panel + tray icon).
echo Or double-click start.bat if you prefer the terminal version.
echo Optional: rename .env.example to .env and put your Gemini API key in it.
echo.
pause
exit /b 0
:NO_PYTHON
echo [Python not found]
echo Please install Python 3.11 or 3.12 64-bit from https://www.python.org/downloads/
echo IMPORTANT: tick "Add python.exe to PATH" during setup, then run install.bat again.
pause
exit /b 1
:FAIL
echo.
echo [Install failed]
echo Most common cause: PyAudio failed to build. Make sure Python is 3.11 or 3.12 64-bit, then run install.bat again.
echo Take a screenshot of the red error text above and send it back for help.
pause
exit /b 1
