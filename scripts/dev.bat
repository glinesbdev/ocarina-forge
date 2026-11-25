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

REM Set default environment variables if not already defined
if not defined BANCWRIGHT_HOST set "BANCWRIGHT_HOST=127.0.0.1"
if not defined BANCWRIGHT_PORT set "BANCWRIGHT_PORT=47232"

REM Start the API in the background with a distinct window title for cleanup
start "BancwrightAPI" /b ".venv\Scripts\python.exe" -m bancwright --host "%BANCWRIGHT_HOST%" --port "%BANCWRIGHT_PORT%"

REM Start the frontend in the foreground
pushd frontend
call npm run dev -- --host "%BANCWRIGHT_HOST%" --port 47231 --strictPort
popd

REM Clean up the background API process tree upon exit or Ctrl-C
taskkill /FI "WINDOWTITLE eq BancwrightAPI*" /T /F >nul 2>&1