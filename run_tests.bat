@echo off
rem ============================================================
rem  Lili - run all automated tests
rem  Kept ASCII-only on purpose: cmd.exe can mis-parse batch
rem  files that contain multi-byte characters.
rem ============================================================
setlocal
cd /d "%~dp0"
title Lili - Tests

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
if exist "%VENV_PY%" goto :run

echo [Lili] .venv not found. Please run run_app.bat once first.
pause
exit /b 1

:run
echo ============================================================
echo   Test 1/2 : end-to-end smoke test (data - codegen - Stata)
echo ============================================================
"%VENV_PY%" "tests\smoke_test.py"
set "RC1=%errorlevel%"

echo.
echo ============================================================
echo   Test 2/2 : UI automation test (Streamlit AppTest)
echo ============================================================
"%VENV_PY%" "tests\app_test.py"
set "RC2=%errorlevel%"

echo.
if "%RC1%%RC2%"=="00" (
    echo [Lili] ALL TESTS PASSED.
) else (
    echo [Lili] SOME TESTS FAILED. See the output above.
)
echo.
pause
exit /b 0
