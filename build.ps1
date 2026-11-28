# Restore, build, and test the Godot project. Pass -Dump and -Out to generate a project.
param(
    [string]$Dump = "",
    [string]$Out = "",
    [string]$Godot = ""
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$DotnetUrl = "https://dotnet.microsoft.com/download/dotnet/8.0"
$GodotUrl = "https://github.com/godotengine/godot-builds/releases/download/4.7.2-stable/Godot_v4.7.2-stable_mono_win64.zip"

if (($Dump -ne "") -xor ($Out -ne "")) {
    Write-Error "Pass both -Dump and -Out, or neither. -Dump is the folder that contains romfs\, or the romfs folder itself."
}

$env:DOTNET_CLI_TELEMETRY_OPTOUT = "1"

if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    Write-Error "The .NET 8 SDK was not found on PATH. Install it from $DotnetUrl"
}
$sdks = & dotnet --list-sdks
if (-not ($sdks | Where-Object { $_ -match '^8\.' })) {
    Write-Error "dotnet is on PATH, and the .NET 8 SDK is not. Install it from $DotnetUrl"
}

if ($Godot -eq "") { $Godot = $env:GODOT_BIN }
if (-not $Godot) { $Godot = $env:GODOT }

function Find-GodotOnPath {
    foreach ($name in @("godot", "godot4", "Godot", "Godot_v4.7.2-stable_mono_win64.exe")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    return $null
}

$godotBin = $null
if ($Godot -and (Test-Path -LiteralPath $Godot)) {
    $godotBin = (Resolve-Path -LiteralPath $Godot).Path
} else {
    $godotBin = Find-GodotOnPath
}

if ($godotBin) {
    $version = & $godotBin --version
    if ($version -notmatch '^4\.') {
        Write-Error "Godot at $godotBin reported '$version'. This project needs Godot 4.x .NET (mono). Download $GodotUrl"
    }
    if ($version -notmatch 'mono') {
        Write-Error "Godot at $godotBin reported '$version'. That build is not the .NET (mono) editor. Download $GodotUrl"
    }
    Write-Host "godot $godotBin ($version)"
} elseif ($Dump -ne "") {
    Write-Error "Godot 4.x .NET (mono) was not found. Pass -Godot, or set GODOT_BIN. Download $GodotUrl"
} else {
    Write-Host "Godot 4.x .NET (mono) was not found. Restore, build, and the Godot-free tests still run."
    Write-Host "Headless generate and the viewer self-test are skipped."
    Write-Host "Pass -Godot, or set GODOT_BIN. Download $GodotUrl"
}

& dotnet restore godot\OcarinaForge.sln --nologo
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& dotnet build godot\OcarinaForge.sln -c Debug --nologo
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& dotnet test godot\Forge.Import.Tests\Forge.Import.Tests.csproj --nologo --no-build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$projectTest = @("test", "godot\Forge.Project.Tests\Forge.Project.Tests.csproj", "--nologo")
if ($godotBin) {
    & $godotBin --headless --path godot\viewer -- --self-test
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $projectTest += @("--environment", "GODOT=$godotBin", "--environment", "REQUIRE_GODOT=1")
}
& dotnet @projectTest
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($Dump -ne "") {
    & dotnet run --project godot\Forge.Cli --no-build -- new $Dump $Out --godot $godotBin
    exit $LASTEXITCODE
}
