@echo off
title Jarvis Fullscreen HUD Preview
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe goto NOT_INSTALLED
start "" .venv\Scripts\pythonw.exe web_fullscreen.py --preview
exit /b 0
:NOT_INSTALLED
echo Please run install.bat first.
pause
exit /b 1
