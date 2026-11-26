@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\builder.ps1" %*
if errorlevel 1 (
    echo.
    echo Forge did not finish. Press any key to close this window.
    pause >nul
    exit /b 1
)
exit /b 0
