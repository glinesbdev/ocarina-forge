#!/usr/bin/env bash
# Restore, build, and test the Godot project. Pass --dump and --out to generate a project.
set -euo pipefail
cd "$(dirname "$0")"

DUMP=""
OUT=""
GODOT_ARG="${GODOT_BIN:-${GODOT:-}}"
DOTNET_URL="https://dotnet.microsoft.com/download/dotnet/8.0"
GODOT_URL="https://github.com/godotengine/godot-builds/releases/download/4.7.2-stable/Godot_v4.7.2-stable_mono_linux_x86_64.zip"

usage() {
  echo "usage: ./build.sh [--godot <Godot 4 .NET binary>] [--dump <dump>] --out <project>"
  echo "  GODOT_BIN or GODOT names the binary when --godot is omitted."
  echo "  --dump is the folder that contains romfs/, or the romfs folder itself."
  echo "  Without --dump and --out, this restores, builds, and tests."
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dump)
      [[ $# -ge 2 ]] || { echo "--dump needs a path"; exit 2; }
      DUMP="$2"; shift 2 ;;
    --out)
      [[ $# -ge 2 ]] || { echo "--out needs a path"; exit 2; }
      OUT="$2"; shift 2 ;;
    --godot)
      [[ $# -ge 2 ]] || { echo "--godot needs a path"; exit 2; }
      GODOT_ARG="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument $1"; usage; exit 2 ;;
  esac
done

if [[ -n "$DUMP" && -z "$OUT" ]] || [[ -z "$DUMP" && -n "$OUT" ]]; then
  echo "Pass both --dump and --out, or neither."
  exit 2
fi

export DOTNET_CLI_TELEMETRY_OPTOUT=1

if ! command -v dotnet >/dev/null 2>&1; then
  echo "The .NET 8 SDK was not found on PATH."
  echo "Install it from ${DOTNET_URL}"
  exit 1
fi
if ! dotnet --list-sdks | grep -q '^8\.'; then
  echo "dotnet is on PATH, and the .NET 8 SDK is not."
  echo "Install it from ${DOTNET_URL}"
  exit 1
fi

find_godot() {
  if [[ -n "$GODOT_ARG" && -f "$GODOT_ARG" ]]; then
    printf '%s\n' "$GODOT_ARG"
    return 0
  fi
  local name
  for name in godot godot4 Godot; do
    if command -v "$name" >/dev/null 2>&1; then
      command -v "$name"
      return 0
    fi
  done
  return 1
}

GODOT_BIN_FOUND=""
if GODOT_BIN_FOUND="$(find_godot)"; then
  GODOT_VERSION="$("$GODOT_BIN_FOUND" --version 2>/dev/null || true)"
  if [[ "$GODOT_VERSION" != 4.* ]]; then
    echo "Godot at ${GODOT_BIN_FOUND} reported '${GODOT_VERSION}'."
    echo "This project needs Godot 4.x .NET (mono)."
    echo "Download ${GODOT_URL}"
    exit 1
  fi
  if [[ "$GODOT_VERSION" != *mono* ]]; then
    echo "Godot at ${GODOT_BIN_FOUND} reported '${GODOT_VERSION}'."
    echo "That build is not the .NET (mono) editor. The C# scripts need Godot 4.x .NET."
    echo "Download ${GODOT_URL}"
    exit 1
  fi
  echo "godot ${GODOT_BIN_FOUND} (${GODOT_VERSION})"
elif [[ -n "$DUMP" ]]; then
  echo "Godot 4.x .NET (mono) was not found."
  echo "Pass --godot, or set GODOT_BIN to the editor binary."
  echo "Download ${GODOT_URL}"
  exit 1
else
  echo "Godot 4.x .NET (mono) was not found. Restore, build, and the Godot-free tests still run."
  echo "Headless generate and the viewer self-test are skipped."
  echo "Pass --godot, or set GODOT_BIN. Download ${GODOT_URL}"
fi

dotnet restore godot/OcarinaForge.sln --nologo
dotnet build godot/OcarinaForge.sln -c Debug --nologo
dotnet test godot/Forge.Import.Tests/Forge.Import.Tests.csproj --nologo --no-build

project_test=(dotnet test godot/Forge.Project.Tests/Forge.Project.Tests.csproj --nologo)
if [[ -n "$GODOT_BIN_FOUND" ]]; then
  "$GODOT_BIN_FOUND" --headless --path godot/viewer -- --self-test
  project_test+=(--environment "GODOT=${GODOT_BIN_FOUND}" --environment REQUIRE_GODOT=1)
fi
"${project_test[@]}"

if [[ -n "$DUMP" ]]; then
  dotnet run --project godot/Forge.Cli --no-build -- new "$DUMP" "$OUT" --godot "$GODOT_BIN_FOUND"
fi
