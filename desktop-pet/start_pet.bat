@echo off
rem ===========================================================
rem  Desktop pet launcher.
rem
rem  ASCII only and CRLF only on purpose: cmd.exe and Windows
rem  PowerShell 5.1 parse these files using the ANSI codepage, so
rem  non-ASCII bytes can break parsing.
rem
rem  The pet is started detached (no console, outside this process
rem  tree), so closing this window will not stop it.
rem ===========================================================
setlocal
title Desktop Pet Launcher
cd /d "%~dp0"

echo ============================================
echo   Desktop Pet Launcher
echo ============================================
echo Folder : %CD%
echo.

if not exist "%~dp0pet.py" (
    echo [x] pet.py not found in this folder.
    echo.
    pause
    exit /b 1
)

set "PS="
where pwsh.exe >nul 2>&1 && set "PS=pwsh.exe"
if not defined PS (
    where powershell.exe >nul 2>&1 && set "PS=powershell.exe"
)

if not defined PS (
    echo [i] PowerShell not found, falling back to run_pet.py
    echo.
    python "%~dp0run_pet.py" %*
    goto :finished
)

"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher.ps1" %*
set "RC=%errorlevel%"

:finished
echo.
if "%RC%"=="0" (
    echo  Result : STARTED - the pet should be on screen now.
    echo  Closing this window will NOT stop the pet.
) else (
    echo  Result : FAILED ^(exit code %RC%^)
    echo  Details: pet.log / pet_crash.log in this folder
)
echo ============================================
echo.
echo This window closes in 8 seconds.
timeout /t 8 >nul
endlocal
