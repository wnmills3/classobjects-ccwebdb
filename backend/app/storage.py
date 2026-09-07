"""Where image bytes actually live.

**Bytes never go in the database.** A collection's photographs run to
gigabytes, and putting them in PostgreSQL makes every backup, restore and
replica pay for them. The database holds metadata and a storage key.

One interface, so the local filesystem used in development and an
S3-compatible bucket used later are interchangeable to every caller. Keys are
opaque strings; nothing outside this module may assume they are paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .config import settings

__all__ = ["LocalStorage", "StorageBackend", "get_storage"]


class StorageBackend(Protocol):
    """The whole contract. Deliberately tiny."""

    def put(self, key: str, data: bytes) -> None:
        """Store `data` under `key`, replacing anything already there."""
        ...

    def get(self, key: str) -> bytes:
        """Return the bytes stored under `key`, or raise if absent."""
        ...

    def delete(self, key: str) -> None:
        """Remove `key`. Deleting something that is not there is not an error."""
        ...

    def exists(self, key: str) -> bool:
        """Whether anything is stored under `key`."""
        ...


class LocalStorage:
    """Filesystem backend, rooted at a configured directory."""

    def __init__(self, root: Path | None = None) -> None:
        """Root the backend at `root`, or at the configured media directory."""
        self.root = Path(root or settings.media_root)

    def _path(self, key: str) -> Path:
        """Resolve a key to a path, refusing anything that escapes the root.

        A key reaches this from a database row, and a row could in principle
        have been written with `../../etc/passwd`. Resolving and then checking
        containment is the only reliable check -- string inspection misses
        symlinks and mixed separators.
        """
        candidate = (self.root / key).resolve()
        root = self.root.resolve()
        if not candidate.is_relative_to(root):
            raise ValueError(f"storage key escapes the media root: {key!r}")
        return candidate

    def put(self, key: str, data: bytes) -> None:
        """Write `data` at `key`, creating parent directories as needed."""
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temporary name and rename, so a reader never sees a
        # half-written file and a crash leaves no partial object behind.
        temporary = path.with_suffix(path.suffix + ".partial")
        temporary.write_bytes(data)
        temporary.replace(path)

    def get(self, key: str) -> bytes:
        """Read the file at `key`."""
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        """Remove the file at `key` if it is there."""
        self._path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        """Whether a file exists at `key`."""
        return self._path(key).exists()


def get_storage() -> StorageBackend:
    """The configured backend. A FastAPI dependency and a plain function."""
    return LocalStorage()
