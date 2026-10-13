@echo off
rem ============================================================
rem  Lili - Financial Empirical Research Assistant
rem  This launcher is intentionally ASCII-only:
rem  cmd.exe can mis-parse batch files containing multi-byte
rem  characters, which causes "xxx is not recognized" errors and
rem  can even swallow command-line flags. All Chinese messages are
rem  printed by scripts\launcher.py (Python handles UTF-8 safely).
rem ============================================================
setlocal
cd /d "%~dp0"
title Lili - Financial Empirical Research Assistant

set "VENV_DIR=%~dp0.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

rem ---------- 1. reuse an existing virtual environment ----------
if not exist "%VENV_PY%" goto :create_venv
"%VENV_PY%" --version >nul 2>nul
if errorlevel 1 (
    echo [Lili] The existing .venv looks broken. Rebuilding ...
    rmdir /s /q "%VENV_DIR%"
    goto :create_venv
)
goto :run

rem ---------- 2. create .venv on first use ----------
:create_venv
echo [Lili] First run: creating the project virtual environment (.venv) ...
set "BASE_PY="
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 set "BASE_PY=py -3"
if not defined BASE_PY (
    python -c "import sys" >nul 2>nul
    if not errorlevel 1 set "BASE_PY=python"
)
if not defined BASE_PY goto :no_python

%BASE_PY% -m venv "%VENV_DIR%"
if errorlevel 1 goto :venv_failed

rem ---------- 3. start the app ----------
:run
"%VENV_PY%" "scripts\launcher.py"
if errorlevel 1 (
    echo.
    echo [Lili] The application exited with an error. See the messages above.
    pause
)
exit /b 0

:no_python
echo.
echo [Lili] ERROR: Python was not found on this computer.
echo        Please install Python 3.9+ from https://www.python.org/downloads/
echo        and make sure to tick "Add Python to PATH" during installation.
echo.
pause
exit /b 1

:venv_failed
echo.
echo [Lili] ERROR: Failed to create the virtual environment.
echo        Try running this file as Administrator, or run manually:
echo        %BASE_PY% -m venv "%VENV_DIR%"
echo.
pause
exit /b 1
