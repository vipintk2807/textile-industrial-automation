@echo off
rem After a green test run, record the exact dependency versions you tested with.
setlocal
cd /d "%~dp0.."
if not exist .venv\Scripts\activate.bat ( echo Run scripts\setup.bat first. & exit /b 1 )
call .venv\Scripts\activate.bat
python -m pip freeze --exclude-editable > requirements-lock.txt
echo Wrote requirements-lock.txt - commit it so installs are reproducible.
endlocal
