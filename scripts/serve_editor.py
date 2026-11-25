"""Serve the built editor and signal readiness to Longshot's broker.

`frontend/dist` is whatever `npm run build` last wrote. `npm run build:webgl`
never starts WebGPU. index.html is revalidated; restarting this process does
not rebuild the bundle.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from socket import socket

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ocarina_forge.server import app, mount_frontend


class ReadyServer(uvicorn.Server):
    def __init__(self, config: uvicorn.Config, ready_file: Path) -> None:
        super().__init__(config)
        self.ready_file = ready_file
        self.stop_file = ready_file.with_suffix(".stop")

    async def on_tick(self, counter: int) -> bool:
        if self.stop_file.is_file():
            self.should_exit = True
        return await super().on_tick(counter)

    async def startup(self, sockets: list[socket] | None = None) -> None:
        await super().startup(sockets)
        if self.started:
            self.ready_file.write_text("ready\n", encoding="ascii")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the Ocarina Forge WebGL editor for Longshot.")
    parser.add_argument("--ready-file", required=True, type=Path)
    parser.add_argument("--game-root", type=Path)
    args = parser.parse_args(argv)
    if args.game_root:
        os.environ["OCARINA_FORGE_GAME"] = str(args.game_root.expanduser().resolve())
    static = ROOT / "frontend" / "dist"
    if not (static / "index.html").is_file():
        parser.error(f"the WebGL frontend is not built: {static}")
    mount_frontend(static)
    ReadyServer(
        uvicorn.Config(app, host="127.0.0.1", port=47231, reload=False, timeout_graceful_shutdown=2),
        args.ready_file,
    ).run()


if __name__ == "__main__":
    main()
