@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
"%~dp0.venv\Scripts\python.exe" -B "%~dp0oneclick_research.py"
echo Task window can now be closed. Results and logs are saved in the project.
pause
