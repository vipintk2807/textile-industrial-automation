@echo off
rem One-time setup: creates .venv, installs dependencies, creates config\devices.toml.
rem Run from anywhere:  scripts\setup.bat   (needs Python 3.12 or newer and internet access)
setlocal
cd /d "%~dp0.."

where py >nul 2>nul
if errorlevel 1 (
  echo Python launcher "py" was not found. Install Python 3.12+ from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" / "py launcher" in the installer, then run this again.
  exit /b 1
)

if not exist .venv (
  py -3 -m venv .venv
  if errorlevel 1 ( echo Could not create the virtual environment. & exit /b 1 )
)
call .venv\Scripts\activate.bat

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)"
if errorlevel 1 (
  echo This project needs Python 3.12 or newer. You have:
  python --version
  echo Install Python 3.12+, delete the .venv folder, and run this script again.
  exit /b 1
)

python -m pip install --upgrade pip
if errorlevel 1 exit /b 1
python -m pip install -e ".[dev]"
if errorlevel 1 ( echo Dependency installation failed. See the Troubleshooting section of README.md. & exit /b 1 )

if not exist config\devices.toml (
  copy config\devices.example.toml config\devices.toml >nul
  echo Created config\devices.toml from the example. Edit it to change devices.
)
echo.
echo Setup complete. Next: scripts\test.bat   then   scripts\run.bat
endlocal
