@echo off
set PYTHONUTF8=1
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" -B "%~dp0return_menu.py"
if errorlevel 1 pause
