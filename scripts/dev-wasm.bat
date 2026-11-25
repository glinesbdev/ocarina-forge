@echo off
setlocal EnableExtensions
REM Start the editor on 47241/47242 without pip or maturin, and without
REM using 47231/47232.
cd /d "%~dp0.."

REM OCARINA_FORGE_HOST and OCARINA_FORGE_PORT are the names to set.
REM BANCWRIGHT_HOST and BANCWRIGHT_PORT are still read.
if not defined OCARINA_FORGE_HOST if defined BANCWRIGHT_HOST set "OCARINA_FORGE_HOST=%BANCWRIGHT_HOST%"
if not defined OCARINA_FORGE_PORT if defined BANCWRIGHT_PORT set "OCARINA_FORGE_PORT=%BANCWRIGHT_PORT%"
if not defined OCARINA_FORGE_HOST set "OCARINA_FORGE_HOST=127.0.0.1"
if not defined OCARINA_FORGE_PORT set "OCARINA_FORGE_PORT=47242"
if not defined FORGE_VITE_PORT set "FORGE_VITE_PORT=47241"

echo Starting Ocarina Forge on %OCARINA_FORGE_HOST%:%OCARINA_FORGE_PORT% ...
start "ForgeWasmAPI" /b ".venv\Scripts\python.exe" -m ocarina_forge --host "%OCARINA_FORGE_HOST%" --port "%OCARINA_FORGE_PORT%"

pushd frontend
echo Starting Vite on %OCARINA_FORGE_HOST%:%FORGE_VITE_PORT% ...
call npm run dev -- --host "%OCARINA_FORGE_HOST%" --port %FORGE_VITE_PORT% --strictPort
popd

taskkill /FI "WINDOWTITLE eq ForgeWasmAPI*" /T /F >nul 2>&1
