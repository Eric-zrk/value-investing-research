@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
"%~dp0.venv\Scripts\python.exe" -B "%~dp0safety_margin.py"
if errorlevel 1 echo Screen did not complete. Please keep the error message.
pause
