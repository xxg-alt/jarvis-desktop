@echo off
title Jarvis Text Mode
cd /d "%~dp0"
if not exist .venv goto NOT_INSTALLED
call .venv\Scripts\activate.bat
python jarvis.py --text
pause
exit /b 0
:NOT_INSTALLED
echo Not installed yet. Please double-click install.bat first.
pause
exit /b 1
