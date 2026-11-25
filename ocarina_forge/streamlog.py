"""Per-model convert timings and the server's running summary.

A convert records wall time and the time spent actually decoding textures.
A cache hit still reports, with the decode time left at zero, so a warm open
is distinguishable from a cold one. The summary is what ``GET /api/stream``
returns and what the server process prints.
"""

from __future__ import annotations

import contextvars
import logging
import threading
import time

_CLOCK: contextvars.ContextVar[ConvertClock | None] = contextvars.ContextVar("forge_convert_clock", default=None)
_log = logging.getLogger("uvicorn.error")


class ConvertClock:
    """One model conversion. Texture time nests, so a layer inside a file counts once."""

    def __init__(self) -> None:
        self.started = time.perf_counter()
        self.texture_s = 0.0
        self._depth = 0
        self._mark = 0.0

    def begin_texture(self) -> None:
        if self._depth == 0:
            self._mark = time.perf_counter()
        self._depth += 1

    def end_texture(self) -> None:
        if self._depth <= 0:
            return
        self._depth -= 1
        if self._depth == 0:
            self.texture_s += time.perf_counter() - self._mark

    def finish(self, cache: str) -> dict:
        total = time.perf_counter() - self.started
        return {
            "cache": cache,
            "convertMs": round(total * 1000, 2),
            "textureMs": round(self.texture_s * 1000, 2),
        }


def bind_clock(clock: ConvertClock):
    return _CLOCK.set(clock)


def reset_clock(token) -> None:
    _CLOCK.reset(token)


def current_clock() -> ConvertClock | None:
    return _CLOCK.get()


class StreamLog:
    """The last few converts, plus totals for the ones that missed the glb cache."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.rows: list[str] = []
        self.count = 0
        self.hits = 0
        self.misses = 0
        self.convert_ms = 0.0
        self.texture_ms = 0.0

    def record(self, kind: str, name: str, timing: dict) -> str:
        cache = timing.get("cache") or "miss"
        line = (
            f"{kind} {name} cache={cache} "
            f"convert={timing.get('convertMs')}ms texture={timing.get('textureMs')}ms"
        )
        with self._lock:
            self.count += 1
            if cache == "hit":
                self.hits += 1
            else:
                self.misses += 1
                self.convert_ms += float(timing.get("convertMs") or 0)
                self.texture_ms += float(timing.get("textureMs") or 0)
            self.rows.append(line)
            del self.rows[:-40]
        _log.info(line)
        return line

    def summary(self) -> dict:
        with self._lock:
            return {
                "count": self.count,
                "hits": self.hits,
                "misses": self.misses,
                "convertMs": round(self.convert_ms, 1),
                "textureMs": round(self.texture_ms, 1),
                "lines": list(self.rows),
            }


STREAM = StreamLog()
