@echo off
rem Formatting, lint, type and static checks. Run once after setup; fix anything reported.
setlocal
cd /d "%~dp0.."
if not exist .venv\Scripts\activate.bat ( echo Run scripts\setup.bat first. & exit /b 1 )
call .venv\Scripts\activate.bat
python -m ruff format .
python -m ruff check .
python -m mypy src
python scripts\static_check.py
endlocal
