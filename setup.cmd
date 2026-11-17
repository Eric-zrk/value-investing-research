@echo off
setlocal
set PYTHONUTF8=1
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NoExit -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
pause
