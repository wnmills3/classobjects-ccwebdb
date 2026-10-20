"""Local time: TIME_ZONE's, or the host's own zone when it names none.

Every stored moment is UTC. Local time belongs only where a moment becomes a
calendar day or a name -- "today" in a report, the day a sale falls on, a
backup file's stamp -- and every such place asks here, so a server running
on UTC can still keep its owner's days by setting TIME_ZONE.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from .config import settings


def _zone() -> ZoneInfo | None:
    """TIME_ZONE's zone, or None for the host's own."""
    return ZoneInfo(settings.time_zone) if settings.time_zone else None


def local_now() -> datetime:
    """Now, as an aware time in the local zone."""
    zone = _zone()
    return datetime.now(zone) if zone else datetime.now().astimezone()


def local_today() -> date:
    """Today's date in the local zone."""
    return local_now().date()


def to_local(moment: datetime) -> datetime:
    """An aware `moment` in the local zone: the same instant, local clock."""
    zone = _zone()
    return moment.astimezone(zone) if zone else moment.astimezone()


def assume_local(moment: datetime) -> datetime:
    """A wall-clock time with no zone, read as the local zone's clock."""
    zone = _zone()
    return moment.replace(tzinfo=zone) if zone else moment.astimezone()
