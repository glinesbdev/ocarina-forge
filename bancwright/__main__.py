"""Deprecated. Use ``python -m ocarina_forge``."""

from __future__ import annotations

import sys
import warnings

warnings.warn(
    "python -m bancwright was renamed to python -m ocarina_forge. "
    "This command will be removed in a later release.",
    DeprecationWarning,
    stacklevel=2,
)
print(
    "bancwright was renamed to ocarina_forge. Use python -m ocarina_forge.",
    file=sys.stderr,
)

from ocarina_forge.__main__ import main

if __name__ == "__main__":
    main()
