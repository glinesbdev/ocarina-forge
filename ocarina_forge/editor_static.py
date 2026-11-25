"""Static editor files. HTML must revalidate. Hashed Vite assets can stay cached."""

from __future__ import annotations

import re

from fastapi.staticfiles import StaticFiles

# Vite's default name is `<name>-<base64url hash>.<ext>`. Eight characters is the short hash.
_HASHED_ASSET = re.compile(r"-[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9]+)+$")


def cache_control_for(path: str) -> str:
    """`no-cache` revalidates. Immutable is only for fingerprinted files under `assets/`."""
    normal = str(path or "").replace("\\", "/").lstrip("/")
    name = normal.rsplit("/", 1)[-1]
    if not name or name == "index.html":
        return "no-cache"
    if normal.startswith("assets/") and _HASHED_ASSET.search(name):
        return "public, max-age=31536000, immutable"
    return "no-cache"


class EditorStaticFiles(StaticFiles):
    """Starlette's FileResponse sends ETag and Last-Modified and no Cache-Control.

    Browsers then heuristically cache `index.html`. A stale `frontend/dist`
    keeps serving the bundle that constructed `WebGLRenderer`.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = cache_control_for(path)
        return response
