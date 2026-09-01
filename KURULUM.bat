@echo off
setlocal
cd /d "%~dp0"
title ProxyPulse Kurulum
echo [1/3] Python denetleniyor...
where py >nul 2>nul
if errorlevel 1 (
  echo Python bulunamadi. Python 3.11 veya daha yenisini kurun.
  pause
  exit /b 1
)
echo [2/3] Sanal ortam hazirlaniyor...
if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv
if errorlevel 1 goto :fail
echo [3/3] Kutuphaneler kuruluyor...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :fail
echo.
echo Kurulum tamamlandi. BASLAT.bat dosyasini acabilirsiniz.
pause
exit /b 0
:fail
echo.
echo Kurulum sirasinda hata olustu.
pause
exit /b 1

