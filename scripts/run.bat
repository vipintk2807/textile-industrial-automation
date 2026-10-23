@echo off
rem Starts the monitoring application (simulator) and opens it in your browser.
setlocal
cd /d "%~dp0.."
if not exist .venv\Scripts\activate.bat ( echo Run scripts\setup.bat first. & exit /b 1 )
call .venv\Scripts\activate.bat
python -m streamlit run app.py
endlocal
