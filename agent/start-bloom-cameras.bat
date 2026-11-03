@echo off
REM Bloom Camera Agent - Meridian Zero. Starts with Windows (Task Scheduler) and restarts if it stops.
cd /d "%~dp0"
:loop
python bloom_camera_agent.py
timeout /t 30 /nobreak >nul
goto loop
