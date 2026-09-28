"""The report registry, the result shape, and the shared live-row predicate."""

from __future__ import annotations

import pytest
from app.live import live_item
from app.models import InventoryItem
from app.models.base import utcnow
from app.reports import REPORTS, register
from app.reports.base import Column, Report, ReportResult
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.builders import build_bare_item


class _NoParams(BaseModel):
    """A report that takes no parameters."""


def _run_noop(db: Session, params: BaseModel) -> ReportResult:
    return ReportResult(columns=[Column("n", "Count", "count")], rows=[])


def _make_report(report_id: str) -> Report:
    return Report(
        id=report_id,
        group="test",
        title="No-op",
        purpose="Exercises the registry.",
        params=_NoParams,
        run=_run_noop,
    )


def test_a_registered_report_is_found_by_id() -> None:
    report = _make_report("test_noop")
    register(report)
    try:
        assert REPORTS["test_noop"] is report
    finally:
        del REPORTS["test_noop"]


def test_a_duplicate_id_is_refused() -> None:
    report = _make_report("test_dup")
    register(report)
    try:
        with pytest.raises(ValueError, match="test_dup"):
            register(_make_report("test_dup"))
    finally:
        del REPORTS["test_dup"]


def test_a_result_with_mismatched_drills_is_refused() -> None:
    with pytest.raises(ValueError, match="drills"):
        ReportResult(
            columns=[Column("n", "Count", "count")],
            rows=[{"n": 1}],
            drills=[],
        )


def test_live_item_excludes_a_deleted_and_a_split_item(db: Session) -> None:
    live = build_bare_item(db)
    deleted = build_bare_item(db)
    deleted.deleted_at = utcnow()
    split = build_bare_item(db)
    split.split_at = utcnow()
    db.commit()

    ids = set(
        db.scalars(
            select(InventoryItem.id).where(
                live_item(),
                InventoryItem.id.in_([live.id, deleted.id, split.id]),
            )
        )
    )
    assert ids == {live.id}
