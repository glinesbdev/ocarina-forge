#!/usr/bin/env bash
# Set up, build, and launch Ocarina Forge.
# Package installs run only after a yes. Rust is installed only when the wasm module is out of date.
set -euo pipefail
cd "$(dirname "$0")"

confirm() {
  local reply
  printf '%s [y/N] ' "$1"
  if ! read -r reply; then
    return 1
  fi
  [[ "$reply" == [yY] || "$reply" == [yY][eE][sS] ]]
}

# Keep this in sync with linux_family() in ocarina_forge/builder.py.
distro_family() {
  local blob=""
  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    blob="${ID:-} ${ID_LIKE:-}"
  fi
  case "$blob" in
    *debian*|*ubuntu*|*mint*|*pop*) echo apt ;;
    *fedora*|*rhel*|*centos*|*rocky*|*alma*) echo dnf ;;
    *arch*|*manjaro*) echo pacman ;;
    *) echo unknown ;;
  esac
}

# Used only when Python itself is missing, so builder.py cannot print the command yet.
# Keep this in sync with install_commands() in ocarina_forge/builder.py.
python_install_command() {
  case "$(distro_family)" in
    apt) echo "sudo apt-get install -y python3 python3-venv" ;;
    dnf) echo "sudo dnf install -y python3" ;;
    pacman) echo "sudo pacman -S --needed python" ;;
    *) echo "" ;;
  esac
}

run_confirmed() {
  local prompt="$1"
  local cmd="$2"
  if [[ -z "$cmd" ]]; then
    echo "error: this distro has no install command here. Install it, then run ./build.sh again." >&2
    exit 1
  fi
  echo "$cmd"
  if confirm "$prompt"; then
    if ! bash -c "$cmd"; then
      echo "error: that install did not finish." >&2
      exit 1
    fi
    hash -r || true
  else
    echo "error: install skipped. Run ./build.sh again when it is installed." >&2
    exit 1
  fi
}

find_python() {
  local cmd
  for cmd in python3 python; do
    if command -v "$cmd" >/dev/null 2>&1; then
      if "$cmd" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
        echo "$cmd"
        return 0
      fi
    fi
  done
  return 1
}

check_only=0
for arg in "$@"; do
  if [[ "$arg" == "--check" ]]; then
    check_only=1
  fi
done

if [[ -f "$HOME/.cargo/env" ]]; then
  # shellcheck disable=SC1091
  source "$HOME/.cargo/env"
fi

python=""
if ! python="$(find_python)"; then
  cmd="$(python_install_command)"
  echo "Python 3.11 or newer was not found."
  if [[ "$check_only" == 1 ]]; then
    if [[ -n "$cmd" ]]; then
      echo "$cmd"
    else
      echo "Install Python 3.11 or newer, then run ./build.sh --check again."
    fi
    exit 1
  fi
  run_confirmed "Install Python?" "$cmd"
  if ! python="$(find_python)"; then
    echo "error: Python is still not on PATH. Open a new terminal and run ./build.sh again." >&2
    exit 1
  fi
fi

if [[ "$check_only" == 1 ]]; then
  exec "$python" -m ocarina_forge.builder "$@"
fi

status="$(FORGE_BUILDER_STATUS=shell "$python" -m ocarina_forge.builder --check)"
# shellcheck disable=SC2086
eval "$status"

install_if_missing() {
  local name="$1"
  local cmd="$2"
  local prompt="$3"
  case " ${missing:-} " in
    *" ${name} "*) run_confirmed "$prompt" "$cmd" ;;
  esac
}

install_if_missing python "${python_cmd:-}" "Install Python?"
install_if_missing venv "${venv_cmd:-}" "Install the Python venv module?"
install_if_missing node "${node_cmd:-}" "Install Node?"
install_if_missing npm "${npm_cmd:-}" "Install npm?"
install_if_missing linker "${linker_cmd:-}" "Install a C linker?"

case " ${missing:-} " in
  *" rust "*)
    echo "${rust_cmd:-}"
    if confirm "Install Rust with rustup?"; then
      if ! bash -c "${rust_cmd:-}"; then
        echo "error: rustup did not finish." >&2
        exit 1
      fi
    else
      echo "error: Rust is required to rebuild the wasm module." >&2
      exit 1
    fi
    ;;
esac

if [[ -f "$HOME/.cargo/env" ]]; then
  # shellcheck disable=SC1091
  source "$HOME/.cargo/env"
fi
hash -r || true
unset FORGE_BUILDER_STATUS

exec "$python" -m ocarina_forge.builder "$@"
