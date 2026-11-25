@echo off
setlocal EnableExtensions
cd /d "%~dp0.."

if not exist "tools\wasm-bench\data\hyrule-field-child.bin" (
  echo Exporting fixtures from the game dump...
  if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" tools\export_wasm_fixture.py --dump "E:\sw2\oot\stable\ray-test"
  ) else (
    python tools\export_wasm_fixture.py --dump "E:\sw2\oot\stable\ray-test"
  )
  if errorlevel 1 exit /b 1
)

echo Building the wasm module...
pushd native
cargo build --release --target wasm32-unknown-unknown --no-default-features --features wasm
if errorlevel 1 popd & exit /b 1
wasm-bindgen target\wasm32-unknown-unknown\release\ocarina_forge_native.wasm --out-dir ..\tools\wasm-bench\pkg --target web
if errorlevel 1 popd & exit /b 1
popd

pushd tools\wasm-bench
if not exist node_modules (
  call npm install
  if errorlevel 1 popd & exit /b 1
)
echo Starting the bench at http://127.0.0.1:47241 ...
call npm run bench
set EXIT=%ERRORLEVEL%
popd
exit /b %EXIT%
