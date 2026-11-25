@echo off
setlocal enabledelayedexpansion

REM Navigate to the parent directory of this script
cd /d "%~dp0.."

REM Setup Python virtual environment if missing
if not exist ".venv\" (
    python -m venv .venv
    if errorlevel 1 exit /b 1
    ".venv\Scripts\pip.exe" install -r requirements.txt
    if errorlevel 1 exit /b 1
)

if not defined OCARINA_FORGE_NATIVE_DLL_DIR (
    for /f "delims=" %%D in ('where.exe libunwind.dll 2^>nul') do (
        if not defined OCARINA_FORGE_NATIVE_DLL_DIR set "OCARINA_FORGE_NATIVE_DLL_DIR=%%~dpD"
    )
)

if not exist ".venv\Scripts\maturin.exe" (
    ".venv\Scripts\python.exe" -m pip install "maturin>=1.7,<2"
    if errorlevel 1 exit /b 1
)

".venv\Scripts\maturin.exe" develop --release --manifest-path native/Cargo.toml
if errorlevel 1 exit /b 1

".venv\Scripts\python.exe" -c "from ocarina_forge.extension import module; import sys; native = module(); print('Rust extension:', getattr(native, '__file__', None)); sys.exit(native is None)"
if errorlevel 1 exit /b 1

REM Setup frontend dependencies if missing
if not exist "frontend\node_modules\" (
    pushd frontend
    call npm install
    if errorlevel 1 (
        popd
        exit /b 1
    )
    popd
)

REM OCARINA_FORGE_HOST and OCARINA_FORGE_PORT are the names to set.
REM BANCWRIGHT_HOST and BANCWRIGHT_PORT are still read so an existing shell keeps its port.
if not defined OCARINA_FORGE_HOST if defined BANCWRIGHT_HOST set "OCARINA_FORGE_HOST=%BANCWRIGHT_HOST%"
if not defined OCARINA_FORGE_PORT if defined BANCWRIGHT_PORT set "OCARINA_FORGE_PORT=%BANCWRIGHT_PORT%"
if not defined OCARINA_FORGE_HOST set "OCARINA_FORGE_HOST=127.0.0.1"
if not defined OCARINA_FORGE_PORT set "OCARINA_FORGE_PORT=47232"

REM Start the API in the background with a distinct window title for cleanup
start "OcarinaForgeAPI" /b ".venv\Scripts\python.exe" -m ocarina_forge --host "%OCARINA_FORGE_HOST%" --port "%OCARINA_FORGE_PORT%"

REM Start the frontend in the foreground
pushd frontend
call npm run dev -- --host "%OCARINA_FORGE_HOST%" --port 47231 --strictPort
popd

REM Clean up the background API process tree upon exit or Ctrl-C
taskkill /FI "WINDOWTITLE eq OcarinaForgeAPI*" /T /F >nul 2>&1