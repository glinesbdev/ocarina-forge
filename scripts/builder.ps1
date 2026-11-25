# Forge builder for Windows. Finds Python, Node, Rust, and the MSVC build tools,
# asks before every install, refreshes PATH, then runs ocarina_forge.builder.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$BuilderArgs = @($args)

function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = New-Object System.Collections.Generic.List[string]
    $cargo = Join-Path $env:USERPROFILE ".cargo\bin"
    if (Test-Path $cargo) { $parts.Add($cargo) }
    foreach ($entry in @($user, $machine)) {
        if (-not $entry) { continue }
        foreach ($piece in ($entry -split ";")) {
            if ($piece -and -not $parts.Contains($piece)) { $parts.Add($piece) }
        }
    }
    $env:Path = ($parts -join ";")
}

function Confirm-Forge([string]$Question) {
    $answer = Read-Host "$Question [y/N]"
    return $answer -match "^(y|yes)$"
}

function Find-Python {
    $candidates = @(
        @{ Exe = "py"; Extra = @("-3") },
        @{ Exe = "python"; Extra = @() },
        @{ Exe = "python3"; Extra = @() }
    )
    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.Exe -ErrorAction SilentlyContinue)) { continue }
        $previous = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $version = & $candidate.Exe @($candidate.Extra) -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        $code = $LASTEXITCODE
        $ErrorActionPreference = $previous
        if ($code -ne 0 -or -not $version) { continue }
        $numbers = $version.Trim().Split(".")
        $major = [int]$numbers[0]
        $minor = [int]$numbers[1]
        if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 11)) {
            return $candidate
        }
    }
    return $null
}

function Test-Missing($status, [string]$Name) {
    return @($status.missing) -contains $Name
}

function Install-WingetId([string]$Id, [string]$Prompt, [string]$Shown) {
    Write-Host $Shown
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host "winget was not found, so this cannot be installed automatically."
        exit 1
    }
    if (-not (Confirm-Forge $Prompt)) {
        Write-Host "error: install skipped."
        exit 1
    }
    winget install --id $Id -e --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Host "error: winget exited with $LASTEXITCODE."
        exit $LASTEXITCODE
    }
    Update-SessionPath
}

Update-SessionPath
$checkOnly = $BuilderArgs -contains "--check"
$python = Find-Python
if (-not $python) {
    $shown = "winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements"
    Write-Host "Python 3.11 or newer was not found."
    Write-Host $shown
    if ($checkOnly) { exit 1 }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host "Install Python from https://www.python.org/downloads/ and run build.bat again."
        exit 1
    }
    if (-not (Confirm-Forge "Install Python?")) {
        Write-Host "error: install skipped."
        exit 1
    }
    winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Host "error: the Python installer exited with $LASTEXITCODE."
        exit $LASTEXITCODE
    }
    Update-SessionPath
    $python = Find-Python
    if (-not $python) {
        Write-Host "error: Python is still not on PATH. Open a new terminal and run build.bat again."
        exit 1
    }
}

if ($checkOnly) {
    & $python.Exe @($python.Extra) -m ocarina_forge.builder @BuilderArgs
    exit $LASTEXITCODE
}

$env:FORGE_BUILDER_STATUS = "json"
$previous = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$raw = & $python.Exe @($python.Extra) -m ocarina_forge.builder --check | Out-String
$statusCode = $LASTEXITCODE
$ErrorActionPreference = $previous
Remove-Item Env:\FORGE_BUILDER_STATUS -ErrorAction SilentlyContinue
if ($statusCode -ne 0 -or -not $raw) {
    Write-Host $raw
    exit 1
}
$status = $raw | ConvertFrom-Json

if (Test-Missing $status "venv") {
    Install-WingetId "Python.Python.3.12" "Install the Python venv module?" $status.commands.venv
}
if ((Test-Missing $status "node") -or (Test-Missing $status "npm")) {
    $shown = $status.commands.node
    if (-not $shown) { $shown = $status.commands.npm }
    Install-WingetId "OpenJS.NodeJS.LTS" "Install Node?" $shown
}
if (Test-Missing $status "rust") {
    Write-Host "Rust is required because the wasm module is out of date."
    Write-Host "This downloads rustup-init.exe from https://win.rustup.rs/x86_64 and runs it."
    Write-Host "Alternatively: winget install --id Rustlang.Rustup -e"
    if (-not (Confirm-Forge "Install Rust with rustup?")) {
        Write-Host "error: install skipped."
        exit 1
    }
    $dest = Join-Path $env:TEMP "rustup-init.exe"
    Write-Host "  Downloading rustup-init.exe"
    $oldProgress = $ProgressPreference
    $ProgressPreference = "SilentlyContinue"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    try {
        Invoke-WebRequest -Uri "https://win.rustup.rs/x86_64" -OutFile $dest
    } finally {
        $ProgressPreference = $oldProgress
    }
    & $dest -y --default-toolchain 1.83.0
    if ($LASTEXITCODE -ne 0) {
        Write-Host "rustup-init exited with $LASTEXITCODE."
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            if (Confirm-Forge "Install Rust with winget instead?") {
                winget install --id Rustlang.Rustup -e --accept-package-agreements --accept-source-agreements
            }
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Host "error: Rust was not installed."
            exit 1
        }
    }
    Update-SessionPath
}
if (Test-Missing $status "linker") {
    Write-Host "Rust's MSVC toolchain needs the Visual Studio C++ build tools."
    Write-Host $status.commands.linker
    Write-Host "Or install the C++ workload from https://visualstudio.microsoft.com/visual-cpp-build-tools/"
    if (-not (Confirm-Forge "Install the Visual Studio C++ Build Tools?")) {
        Write-Host "error: install skipped."
        exit 1
    }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host "winget was not found. Install the C++ workload from the link above, then run build.bat again."
        exit 1
    }
    winget install --id Microsoft.VisualStudio.2022.BuildTools -e --accept-package-agreements --accept-source-agreements --override "--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "error: the Visual Studio installer exited with $LASTEXITCODE. A reboot may be required."
        exit $LASTEXITCODE
    }
    Update-SessionPath
}

& $python.Exe @($python.Extra) -m ocarina_forge.builder @BuilderArgs
exit $LASTEXITCODE
