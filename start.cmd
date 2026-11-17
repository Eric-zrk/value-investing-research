@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist "%~dp0.venv\Scripts\python.exe" goto missing
if /i "%~1"=="--check" goto check
echo AKShare - A-share and Hong Kong financial statements
echo Market: a = A-share, hk = Hong Kong
set "market="
set "symbol="
set /p "market=Market (a/hk): "
set /p "symbol=Stock code (6 digits for A-share, 5 for HK): "
"%~dp0.venv\Scripts\python.exe" -B "%~dp0fetch_financials.py" --market "%market%" --symbol "%symbol%"
echo.
echo Results are saved in the data folder next to this file.
echo If an error appears above, please share a screenshot.
pause
exit /b
:check
"%~dp0.venv\Scripts\python.exe" -B "%~dp0fetch_financials.py" --help
exit /b %errorlevel%
:missing
echo Python environment not found. Run setup.cmd in this project folder first.
pause
exit /b 1
