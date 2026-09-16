r"""Write a process's output to log files of bounded size and number.

The development runtime (scripts\ccweb_startup.cmd, ccweb_pgstart.cmd) pipes
the backend's, the frontend's and PostgreSQL's console output through this,
so each log type keeps a bounded number of files of bounded size
(logs\README.md):

    uvicorn ... 2>&1 | python backend\app\logpipe.py logs\backend.log

``backend.log`` is the current file; ``backend.1.log`` the one before it and
``backend.2.log`` the one before that. A new file is started on every launch
and whenever the current one would pass the size limit.

It can also only start a new file, for one a program writes itself:

    python backend\app\logpipe.py logs\pg_stop.log --rotate

Run as a plain script, not as part of the app package, so it needs nothing
but the standard library. Settings come from the environment:

    CCWEB_LOG_KEEP       files kept per log type, current one included (3)
    CCWEB_LOG_MAX_BYTES  size at which a file rolls over (1 GiB)
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import BinaryIO

DEFAULT_KEEP = 3
DEFAULT_MAX_BYTES = 1024**3


def _setting(name: str, default: int, minimum: int) -> int:
    """An integer from the environment, refusing nonsense rather than using it."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise SystemExit(f"{name}={raw!r} is not a whole number") from None
    if value < minimum:
        raise SystemExit(f"{name}={value} is below the minimum of {minimum}")
    return value


def keep_setting() -> int:
    """How many files each log type keeps."""
    return _setting("CCWEB_LOG_KEEP", DEFAULT_KEEP, 1)


def max_bytes_setting() -> int:
    """The size at which a log file rolls over."""
    return _setting("CCWEB_LOG_MAX_BYTES", DEFAULT_MAX_BYTES, 1024)


def numbered(path: Path, n: int) -> Path:
    """``backend.log`` -> ``backend.<n>.log``; n = 0 is the file itself."""
    return path if n == 0 else path.with_name(f"{path.stem}.{n}{path.suffix}")


def rotate(path: Path, keep: int) -> None:
    """Shift the numbered files up one and drop the oldest past ``keep``.

    Afterwards ``path`` does not exist. An empty current file is removed
    rather than kept, so restarting a program that wrote nothing does not
    push a useful log out.
    """
    if path.exists() and path.stat().st_size == 0:
        path.unlink()
        return
    oldest = numbered(path, keep - 1)
    if oldest.exists():
        oldest.unlink()
    for n in range(keep - 2, -1, -1):
        source = numbered(path, n)
        if source.exists():
            source.replace(numbered(path, n + 1))


class RollingWriter:
    """Appends bytes to a file, rolling it over before it passes a size."""

    def __init__(self, path: Path, keep: int, max_bytes: int) -> None:
        """Start a fresh file, keeping the previous ones."""
        self.path = path
        self.keep = keep
        self.max_bytes = max_bytes
        path.parent.mkdir(parents=True, exist_ok=True)
        rotate(path, keep)
        self._file: BinaryIO = path.open("ab")
        self._size = 0

    def write(self, chunk: bytes) -> None:
        """Write one line (or chunk), rolling over first if it would not fit."""
        if self._size and self._size + len(chunk) > self.max_bytes:
            self._file.close()
            rotate(self.path, self.keep)
            self._file = self.path.open("ab")
            self._size = 0
        self._file.write(chunk)
        self._file.flush()
        self._size += len(chunk)

    def close(self) -> None:
        """Close the current file."""
        self._file.close()


def pipe(lines: Iterable[bytes], writer: RollingWriter) -> None:
    """Copy every line to the writer until the input ends."""
    try:
        for line in lines:
            writer.write(line)
    finally:
        writer.close()


def main(argv: list[str] | None = None) -> int:
    """Pipe stdin to a rolling log, or only start a new log file."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("target", help="the log file")
    parser.add_argument(
        "--rotate",
        action="store_true",
        help="only start a new TARGET file, keeping the previous ones",
    )
    args = parser.parse_args(argv)
    keep = keep_setting()
    target = Path(args.target)

    if args.rotate:
        target.parent.mkdir(parents=True, exist_ok=True)
        rotate(target, keep)
        return 0

    writer = RollingWriter(target, keep, max_bytes_setting())
    # Binary, so whatever encoding the program writes is kept as it is.
    pipe(sys.stdin.buffer, writer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
