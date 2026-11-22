"""The optional native extension. A missing build keeps the Python paths."""

from __future__ import annotations

_MODULE = None
_TRIED = False


def module():
    """``ocarina_forge_native``, or None when maturin has not built it."""
    global _MODULE, _TRIED
    if not _TRIED:
        _TRIED = True
        try:
            import ocarina_forge_native as native
        except ImportError:
            native = None
        _MODULE = native
    return _MODULE
