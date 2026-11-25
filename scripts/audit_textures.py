#!/usr/bin/env python3
"""Sweep every model in a dump and report how albedo matching turned out.

Usage, from the repo root:

    .venv/bin/python scripts/audit_textures.py <dump> [name fragment]

<dump> is the folder that contains romfs/, or romfs/ itself. The optional
fragment keeps only model paths that contain it, which is how a synthetic
corpus is counted apart from the rest of a fixture.

The JSON on stdout is counts for resolved-via-sampler, resolved-via-Tex-fallback,
and flat colour, plus flat rows grouped by cause. Examples name the model,
the material, the sampler binds, and the paths that were tried. The report
does not contain texture bytes.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ocarina_forge.textureaudit import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
