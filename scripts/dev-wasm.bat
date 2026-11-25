@echo off
setlocal EnableExtensions
REM Start the forge-wasm editor on 47241/47242 without touching the shared .venv
REM (no pip, no maturin develop) and without using 47231/47232, which may be
REM held by a forge-height-check server.
cd /d "%~dp0.."

if not defined BANCWRIGHT_HOST set "BANCWRIGHT_HOST=127.0.0.1"
if not defined BANCWRIGHT_PORT set "BANCWRIGHT_PORT=47242"
if not defined FORGE_VITE_PORT set "FORGE_VITE_PORT=47241"

echo Starting bancwright on %BANCWRIGHT_HOST%:%BANCWRIGHT_PORT% ...
start "ForgeWasmAPI" /b ".venv\Scripts\python.exe" -m bancwright --host "%BANCWRIGHT_HOST%" --port "%BANCWRIGHT_PORT%"

pushd frontend
echo Starting Vite on %BANCWRIGHT_HOST%:%FORGE_VITE_PORT% ...
call npm run dev -- --host "%BANCWRIGHT_HOST%" --port %FORGE_VITE_PORT% --strictPort
popd

taskkill /FI "WINDOWTITLE eq ForgeWasmAPI*" /T /F >nul 2>&1
