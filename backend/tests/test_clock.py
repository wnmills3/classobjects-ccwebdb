"""Local time is TIME_ZONE's, or the host's when it names none.

A server running on UTC would otherwise put an evening's purchases on the
next day in every report.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from app import clock
from app.config import Settings, settings

#: UTC+14 all year. Chosen so its date differs from both UTC's and that of
#: any zone a development machine is likely to be in: a test that passed
#: because TIME_ZONE was ignored would need a host in the middle of the
#: Pacific.
FAR = "Pacific/Kiritimati"

#: Noon UTC on 20 October is already the 21st at UTC+14.
MOMENT = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


@pytest.fixture
def far(monkeypatch: pytest.MonkeyPatch) -> None:
    """TIME_ZONE names a zone no host here is in."""
    monkeypatch.setattr(settings, "time_zone", FAR)


def test_a_named_zone_decides_the_date(far: None) -> None:
    """The calendar day is the zone's, not UTC's or the host's."""
    assert clock.to_local(MOMENT).date() == date(2026, 10, 21)
    assert clock.to_local(MOMENT) == MOMENT


def test_no_zone_keeps_the_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset, nothing changes: the host's own zone, as before TIME_ZONE."""
    monkeypatch.setattr(settings, "time_zone", "")
    assert clock.to_local(MOMENT).utcoffset() == MOMENT.astimezone().utcoffset()
    assert clock.local_now().utcoffset() == datetime.now().astimezone().utcoffset()


def test_now_and_today_are_in_the_zone(far: None) -> None:
    """Both carry the zone, so today is the zone's today."""
    now = clock.local_now()
    assert now.utcoffset() == datetime.now(ZoneInfo(FAR)).utcoffset()
    assert clock.local_today() == datetime.now(ZoneInfo(FAR)).date()


def test_a_moment_with_no_zone_is_read_as_the_zones(far: None) -> None:
    """A spreadsheet cell holds wall-clock time; it means TIME_ZONE's clock."""
    assert clock.assume_local(datetime(2026, 10, 21, 2, 0)) == MOMENT


def test_an_unknown_zone_is_refused() -> None:
    """A typo must stop the API, not leave it on the host's zone."""
    with pytest.raises(ValueError, match="time_zone"):
        Settings(jwt_secret="k" * 32, time_zone="Mars/Olympus")
