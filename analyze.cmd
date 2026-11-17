@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist "%~dp0.venv\Scripts\python.exe" goto missing
"%~dp0.venv\Scripts\python.exe" -B "%~dp0score_menu.py"
if errorlevel 1 pause
exit /b
:missing
echo Python environment missing. Run setup.cmd first.
pause
