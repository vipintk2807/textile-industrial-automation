@echo off
rem Runs the automated test suite.
setlocal
cd /d "%~dp0.."
if not exist .venv\Scripts\activate.bat ( echo Run scripts\setup.bat first. & exit /b 1 )
call .venv\Scripts\activate.bat
python -m pytest %*
endlocal
