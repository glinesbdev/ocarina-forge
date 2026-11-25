@echo off
setlocal EnableExtensions
cd /d "%~dp0.."
echo Building the wasm module...
pushd native
cargo build --release --target wasm32-unknown-unknown --no-default-features --features wasm
if errorlevel 1 popd & exit /b 1
if not exist ..\frontend\wasm mkdir ..\frontend\wasm
wasm-bindgen target\wasm32-unknown-unknown\release\ocarina_forge_native.wasm --out-dir ..\frontend\wasm --target web
if errorlevel 1 popd & exit /b 1
if exist ..\tools\wasm-bench (
  wasm-bindgen target\wasm32-unknown-unknown\release\ocarina_forge_native.wasm --out-dir ..\tools\wasm-bench\pkg --target web
)
popd
echo Wrote frontend\wasm and tools\wasm-bench\pkg
exit /b 0
