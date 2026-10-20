"""Attempt limits for sign-in and registration.

Without them a stranger can guess passwords against sign-in as fast as the
server answers, and test addresses against registration -- whose 409 for a
taken address says the address has an account -- by the thousand.

Counted in this process's memory. The API runs as one uvicorn process
behind Caddy (`docs/deployment-windows.md`), so one count is every count; a restart
clears them, which costs an attacker nothing they could not get by waiting
out the window. The address is the caller's as uvicorn sees it, which with
`--proxy-headers` behind Caddy is the browser's, not Caddy's.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock

from fastapi import HTTPException, status


@dataclass
class Limit:
    """At most `attempts` within `window_seconds`, per key."""

    attempts: int
    window_seconds: float
    _seen: dict[str, deque[float]] = field(default_factory=dict, repr=False)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def _recent(self, key: str, now: float) -> deque[float]:
        """This key's attempts still inside the window, oldest first.

        A key left with none is dropped, so the table holds only callers
        seen within the window.
        """
        seen = self._seen.get(key, deque())
        while seen and seen[0] <= now - self.window_seconds:
            seen.popleft()
        if not seen:
            self._seen.pop(key, None)
        return seen

    def refuse_if_spent(self, *keys: str) -> None:
        """A 429 when any key has used its attempts; otherwise nothing.

        Asked before the attempt is looked at, so a caller over the limit
        learns nothing from it -- not even whether the password was right.
        """
        now = time.monotonic()
        with self._lock:
            recent = [self._recent(key, now) for key in keys]
            waits = [
                seen[0] + self.window_seconds - now
                for seen in recent
                if len(seen) >= self.attempts
            ]
        if waits:
            retry_after = max(1, math.ceil(max(waits)))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many attempts. Try again later.",
                headers={"Retry-After": str(retry_after)},
            )

    def record(self, *keys: str) -> None:
        """Count one attempt against each key."""
        now = time.monotonic()
        with self._lock:
            for key in keys:
                seen = self._recent(key, now)
                seen.append(now)
                self._seen[key] = seen

    def forget(self, *keys: str) -> None:
        """Drop what each key has used."""
        with self._lock:
            for key in keys:
                self._seen.pop(key, None)

    def clear(self) -> None:
        """Drop every count."""
        with self._lock:
            self._seen.clear()


#: Failed sign-ins from one address. A person mistyping a password does not
#: reach ten in a quarter hour; a guesser does at once.
SIGN_IN_FAILURES_BY_ADDRESS = Limit(attempts=10, window_seconds=15 * 60)

#: Failed sign-ins naming one account, from anywhere: a guesser spread over
#: many addresses still meets this. Higher than the per-address limit so a
#: stranger cannot lock the owner out as cheaply as they could guess.
SIGN_IN_FAILURES_BY_ACCOUNT = Limit(attempts=20, window_seconds=15 * 60)

#: Registrations from one address, whatever their answer: each 409 says an
#: address has an account, so the answers themselves are what is limited.
REGISTRATIONS_BY_ADDRESS = Limit(attempts=5, window_seconds=60 * 60)

ALL_LIMITS = (
    SIGN_IN_FAILURES_BY_ADDRESS,
    SIGN_IN_FAILURES_BY_ACCOUNT,
    REGISTRATIONS_BY_ADDRESS,
)
