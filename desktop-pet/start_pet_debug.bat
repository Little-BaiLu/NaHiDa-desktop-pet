@echo off
rem ===========================================================
rem  Desktop pet launcher (debug mode).
rem  Keeps the window open so you can read the output.
rem  ASCII only and CRLF only on purpose (see start_pet.bat).
rem ===========================================================
setlocal
title Desktop Pet Launcher (debug)
cd /d "%~dp0"

echo ============================================
echo   Desktop Pet Launcher - DEBUG
echo ============================================
echo Folder : %CD%
echo.

set "PS="
where pwsh.exe >nul 2>&1 && set "PS=pwsh.exe"
if not defined PS (
    where powershell.exe >nul 2>&1 && set "PS=powershell.exe"
)

if not defined PS (
    echo [i] PowerShell not found, falling back to run_pet.py
    echo.
    python "%~dp0run_pet.py" --debug
    echo.
    pause
    exit /b %errorlevel%
)

"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher.ps1" -KeepOpen -ExtraArgs "--debug"
set "RC=%errorlevel%"

echo.
echo ============================================
echo  Exit code: %RC%
if not "%RC%"=="0" (
    echo  Details : pet.log / pet_crash.log in this folder
)
echo ============================================
echo.
pause
endlocal
