"""Deprecated import path. Use ``ocarina_forge``.

This shim stays for one release so ``import bancwright`` and
``python -m bancwright`` keep working. It will be removed after that.
"""

from __future__ import annotations

import importlib
import sys
import warnings
from pathlib import Path

warnings.warn(
    "bancwright was renamed to ocarina_forge. Import ocarina_forge instead. "
    "This module will be removed in a later release.",
    DeprecationWarning,
    stacklevel=2,
)

import ocarina_forge as _pkg

__version__ = _pkg.__version__


def __getattr__(name: str):
    return getattr(_pkg, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_pkg)))


def _alias_submodules() -> None:
    root = Path(_pkg.__file__).resolve().parent
    for path in root.rglob("*.py"):
        relative = path.relative_to(root).with_suffix("")
        parts = relative.parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if not parts or parts[-1] == "__main__":
            continue
        qualified = "ocarina_forge." + ".".join(parts)
        legacy = "bancwright." + ".".join(parts)
        if legacy not in sys.modules:
            sys.modules[legacy] = importlib.import_module(qualified)


_alias_submodules()
