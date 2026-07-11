@echo off
cd /d D:\Projects\India-BGP-Hijack-Monitor
:loop
python backend\detector\monitor.py >> logs\monitor.log 2>&1
echo %date% %time% monitor.py exited, restarting in 10s >> logs\monitor.log
timeout /t 10 /nobreak >nul
goto loop
