@echo off
title Garmin Sync Server
cd /d "%~dp0"
echo Käivitan Garmin Sync Serveri...
python sync_server.py
pause
