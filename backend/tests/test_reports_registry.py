"""The report registry, the result shape, and the shared live-row predicate."""

from __future__ import annotations

import pytest
from app.live import live_item
from app.models import InventoryItem
from app.models.base import utcnow
from app.reports import REPORTS, register
from app.reports.base import Column, Report, ReportResult
from app.reports.registry import GROUP_ORDER
from app.reports.serialize import catalog
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.builders import build_bare_item


class _NoopParams(BaseModel):
    """A report that takes one parameter -- a real subclass, not `BaseModel`.

    Typing `run` on this exact subclass (not the widened `BaseModel`) is
    what exercises `Report`'s generic parameter: if `Report.run` were still
    `Callable[[Session, BaseModel], ReportResult]`, assigning `_run_noop`
    below (whose second parameter is `_NoopParams`, not `BaseModel`) would
    fail mypy on contravariance grounds.
    """

    limit: int = Field(default=10, title="Limit")


def _run_noop(db: Session, params: _NoopParams) -> ReportResult:
    """Return an empty result; ignores `params` beyond typing against it."""
    return ReportResult(columns=[Column("n", "Count", "count")], rows=[])


def _make_report(report_id: str, group: str = "Money") -> Report[_NoopParams]:
    return Report(
        id=report_id,
        group=group,
        title="No-op",
        purpose="Exercises the registry.",
        params=_NoopParams,
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


def test_the_catalog_groups_come_in_the_stated_order() -> None:
    """The order is stated, not a side effect of import order."""
    assert GROUP_ORDER == (
        "Collection",
        "Data quality",
        "Purchasing and receiving",
        "Selling",
        "Money",
    )
    groups = [report.group for report in REPORTS.values()]
    first_seen = list(dict.fromkeys(groups))
    assert first_seen == list(GROUP_ORDER)
    # Each group's reports sit together: no group reappears after another.
    assert groups == sorted(groups, key=GROUP_ORDER.index)
    assert [entry["id"] for entry in catalog()] == list(REPORTS)


def test_a_report_registered_late_still_sits_in_its_own_group() -> None:
    """A Collection report registered last lists with Collection, not at the end."""
    register(_make_report("test_late", group="Collection"))
    try:
        ids = list(REPORTS)
        collection_ids = [i for i, r in REPORTS.items() if r.group == "Collection"]
        assert collection_ids[-1] == "test_late"
        assert ids.index("test_late") < ids.index("dq_issues")
    finally:
        del REPORTS["test_late"]


def test_a_group_not_in_the_stated_order_is_refused() -> None:
    with pytest.raises(ValueError, match="GROUP_ORDER"):
        register(_make_report("test_nogroup", group="Elsewhere"))
    assert "test_nogroup" not in REPORTS


def test_a_result_with_mismatched_drills_is_refused() -> None:
    with pytest.raises(ValueError, match="drills"):
        ReportResult(
            columns=[Column("n", "Count", "count")],
            rows=[{"n": 1}],
            drills=[],
        )


def test_a_link_column_naming_no_column_is_refused() -> None:
    with pytest.raises(ValueError, match="link_column 'nope'"):
        ReportResult(
            columns=[Column("n", "Count", "count")], rows=[], link_column="nope"
        )


def test_the_linked_column_is_the_named_one_or_else_the_first() -> None:
    columns = [Column("a", "A", "text"), Column("b", "B", "text")]
    assert ReportResult(columns=columns, rows=[]).linked_key == "a"
    assert ReportResult(columns=columns, rows=[], link_column="b").linked_key == "b"
    assert ReportResult(columns=[], rows=[]).linked_key is None


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
