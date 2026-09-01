@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  call KURULUM.bat
  if errorlevel 1 exit /b 1
)
start "ProxyPulse" ".venv\Scripts\pythonw.exe" app.py

