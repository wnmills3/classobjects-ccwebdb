"""`DateRange`: the shared from/to params base every date-scoped report subclasses.

`DateRange` is tested apart from the reports that subclass it (`pr_spend`,
`pr_received`, `mn_tax`, `sl_sales`): each test here registers its own
throwaway report -- `DateRange` with no extra fields -- and unregisters it
afterward, the same pattern `test_reports_registry.py` uses for a report of
its own.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest
from app.reports import REPORTS, register
from app.reports.__main__ import main
from app.reports.base import Column, DateRange, Report, ReportResult
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

_TEST_REPORT_ID = "test_date_range"


class _DateRangeParams(DateRange):
    """A test-only params model: exactly `DateRange`, nothing added."""


def _run_date_range(db: Session, params: _DateRangeParams) -> ReportResult:
    """Echo the resolved bounds back as a one-row result, ignoring `db`."""
    return ReportResult(
        columns=[
            Column("date_from", "From", "date"),
            Column("date_to", "To", "date"),
        ],
        rows=[{"date_from": params.date_from, "date_to": params.date_to}],
        drills=[None],
    )


def _register() -> Report[_DateRangeParams]:
    """Register the test's own date-range report, and return it."""
    return register(
        Report(
            id=_TEST_REPORT_ID,
            group="Money",
            title="Date range",
            purpose="Exercises DateRange.",
            params=_DateRangeParams,
            run=_run_date_range,
        )
    )


@pytest.fixture
def date_report() -> Iterator[Report[_DateRangeParams]]:
    """Register the test-only report, and always unregister it afterward.

    Left registered, it would show up in the catalog and (parametrized at
    collection time, so not a real risk, but kept tidy regardless) the
    performance guard for the rest of the session.
    """
    report = _register()
    try:
        yield report
    finally:
        del REPORTS[_TEST_REPORT_ID]


def test_date_range_defaults_to_no_bound() -> None:
    """Neither bound is required; both default to `None` -- no limit."""
    params = _DateRangeParams()
    assert params.date_from is None
    assert params.date_to is None


def test_date_range_titles_are_from_and_to() -> None:
    """The catalog reads a parameter's label from the field's own `title`."""
    assert DateRange.model_fields["date_from"].title == "From"
    assert DateRange.model_fields["date_to"].title == "To"


def test_date_from_after_date_to_is_refused() -> None:
    """A `date_from` later than `date_to` makes an empty range -- refused."""
    with pytest.raises(ValidationError):
        DateRange(date_from=date(2026, 2, 1), date_to=date(2026, 1, 1))


def test_an_equal_date_from_and_date_to_is_allowed() -> None:
    """A single-day range (`date_from == date_to`) is not an empty range."""
    params = DateRange(date_from=date(2026, 1, 1), date_to=date(2026, 1, 1))
    assert params.date_from == params.date_to == date(2026, 1, 1)


def test_only_one_bound_set_is_never_refused() -> None:
    """One bound with the other absent has nothing to compare -- always fine."""
    DateRange(date_from=date(2026, 1, 1))
    DateRange(date_to=date(2026, 1, 1))


def test_catalog_shows_a_date_field_as_type_date(
    date_report: Report[_DateRangeParams],
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    """The catalog's own `type` for a `date | None` field is `"date"`, default null."""
    res = client.get("/api/reports", headers=admin_headers)
    entry = next(e for e in res.json() if e["id"] == _TEST_REPORT_ID)
    by_name = {p["name"]: p for p in entry["params"]}
    assert by_name["date_from"] == {
        "name": "date_from",
        "label": "From",
        "type": "date",
        "default": None,
        "choices": None,
    }
    assert by_name["date_to"]["label"] == "To"
    assert by_name["date_to"]["type"] == "date"


def test_a_date_round_trips_through_the_api(
    date_report: Report[_DateRangeParams],
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    """`?date_from=2026-01-01` comes back as `"2026-01-01"` in `params` and `rows`."""
    res = client.get(
        f"/api/reports/{_TEST_REPORT_ID}?date_from=2026-01-01",
        headers=admin_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["params"] == {"date_from": "2026-01-01", "date_to": None}
    assert body["rows"] == [{"date_from": "2026-01-01", "date_to": None}]


def test_date_from_after_date_to_is_a_422(
    date_report: Report[_DateRangeParams],
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    res = client.get(
        f"/api/reports/{_TEST_REPORT_ID}?date_from=2026-02-01&date_to=2026-01-01",
        headers=admin_headers,
    )
    assert res.status_code == 422


def test_a_malformed_date_is_a_422(
    date_report: Report[_DateRangeParams],
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    res = client.get(
        f"/api/reports/{_TEST_REPORT_ID}?date_from=not-a-date",
        headers=admin_headers,
    )
    assert res.status_code == 422


def test_cli_param_sets_a_date(
    date_report: Report[_DateRangeParams],
    db: Session,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--param date_from=...` validates and prints the same way as any other."""
    exit_code = main(["run", _TEST_REPORT_ID, "--param", "date_from=2026-01-01"], db=db)
    out, err = capsys.readouterr()

    assert exit_code == 0
    assert err == ""
    assert "From: 2026-01-01" in out


def test_cli_prints_any_for_an_absent_date_bound(
    date_report: Report[_DateRangeParams],
    db: Session,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An absent date bound prints as `any`, matching the console's own heading."""
    exit_code = main(["run", _TEST_REPORT_ID], db=db)
    out, err = capsys.readouterr()

    assert exit_code == 0
    assert err == ""
    assert "From: any" in out
    assert "To: any" in out


def test_cli_date_from_after_date_to_exits_2(
    date_report: Report[_DateRangeParams],
    db: Session,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "run",
            _TEST_REPORT_ID,
            "--param",
            "date_from=2026-02-01",
            "--param",
            "date_to=2026-01-01",
        ],
        db=db,
    )
    _out, err = capsys.readouterr()

    assert exit_code == 2
    assert err != ""


def test_cli_malformed_date_exits_2(
    date_report: Report[_DateRangeParams],
    db: Session,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["run", _TEST_REPORT_ID, "--param", "date_from=not-a-date"], db=db)
    _out, err = capsys.readouterr()

    assert exit_code == 2
    assert err != ""
