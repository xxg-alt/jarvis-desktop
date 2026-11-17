@echo off
title Jarvis GUI
cd /d "%~dp0"
if not exist .venv goto NOT_INSTALLED
start "" .venv\Scripts\pythonw.exe jarvis_gui.py
exit /b 0
:NOT_INSTALLED
echo Not installed yet. Please double-click install.bat first.
pause
exit /b 1
