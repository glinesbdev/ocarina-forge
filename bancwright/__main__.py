"""python -m bancwright  —  start the local server."""

from __future__ import annotations

import argparse
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Bancwright, a visual modding workbench for the Ocarina of Time remake.")
    parser.add_argument("--host", default=os.environ.get("BANCWRIGHT_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("BANCWRIGHT_PORT", "47232")))
    parser.add_argument("--static", help="Serve a built frontend from this directory.")
    args = parser.parse_args()
    if args.static:
        from pathlib import Path

        from bancwright.server import mount_frontend

        mount_frontend(Path(args.static))
    uvicorn.run("bancwright.server:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
