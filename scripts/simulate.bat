@echo off
rem Runs the simulator in this window for 30 seconds (no browser) and prints device status.
setlocal
cd /d "%~dp0.."
if not exist .venv\Scripts\activate.bat ( echo Run scripts\setup.bat first. & exit /b 1 )
call .venv\Scripts\activate.bat
python -m textile_automation run-simulator --seconds 30 --interval 2
endlocal
