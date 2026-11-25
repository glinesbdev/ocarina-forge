"""The optional native extension. A missing build keeps the Python paths."""

from __future__ import annotations

import logging
import os
from pathlib import Path

_LOG = logging.getLogger(__name__)
_WINDOWS = os.name == "nt"
_MODULE = None
_TRIED = False
_DLL_DIRECTORY_HANDLES: list[object] = []


def _native_dll_directory() -> str | None:
    configured = os.environ.get("OCARINA_FORGE_NATIVE_DLL_DIR")
    if configured:
        return configured
    if not _WINDOWS:
        return None
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        directory = Path(entry.strip('"'))
        if (directory / "libunwind.dll").is_file():
            return str(directory)
    return None


def _register_native_dll_directory() -> None:
    if not _WINDOWS:
        return
    directory = _native_dll_directory()
    if directory:
        try:
            handle = os.add_dll_directory(directory)
        except OSError as exc:
            raise RuntimeError(
                f"Could not register OCARINA_FORGE_NATIVE_DLL_DIR {directory!r}"
            ) from exc
        _DLL_DIRECTORY_HANDLES.append(handle)


def module():
    """``ocarina_forge_native``, or None when maturin has not built it."""
    global _MODULE, _TRIED
    if not _TRIED:
        _register_native_dll_directory()
        _TRIED = True
        try:
            import ocarina_forge_native as native
        except ModuleNotFoundError as exc:
            if exc.name != "ocarina_forge_native":
                _LOG.warning("Could not load the Rust extension; using Python fallbacks: %s", exc)
            native = None
        except ImportError as exc:
            _LOG.warning("Could not load the Rust extension; using Python fallbacks: %s", exc)
            native = None
        _MODULE = native
    return _MODULE
