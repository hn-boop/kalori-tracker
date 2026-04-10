@echo off
title Garmin Sync Server
cd /d "%~dp0"

set PYTHON=C:\Users\triin\AppData\Local\Programs\Python\Python311\python.exe

if not exist "%PYTHON%" (
  echo Python ei leitud. Kontrolli installimist.
  pause
  exit /b 1
)

echo Käivitan Garmin Sync Serveri...
"%PYTHON%" sync_server.py
pause
