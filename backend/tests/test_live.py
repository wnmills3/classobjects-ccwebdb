"""The live-row rule, stated twice, must say the same thing.

`app.live.live_item()` is the predicate the reports and Receiving read. The
inventory search views are raw SQL text, so they state the rule as text:
`i.split_at IS NULL` in each `ViewSpec.where`, and `DELETED_MODES["no"]`,
the default. This pins the two as equivalent over every state a row can be
in, so a change to either one alone fails here rather than letting a report
and its own drill-down search quietly disagree about what is in the
collection.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.inventory_search import COIN_VIEW, CURRENCY_VIEW, ViewSpec, search
from app.live import live_item
from app.models import InventoryItem, ItemKind
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.builders import build_bare_item, code_id

_WHEN = datetime(2026, 1, 1, tzinfo=UTC)


def _build_every_state(db: Session, kind_code: str) -> None:
    """One item of `kind_code` in each state: live, deleted, split, a child."""
    kind_id = code_id(db, ItemKind, kind_code)
    build_bare_item(db, item_kind_id=kind_id)
    build_bare_item(db, item_kind_id=kind_id, deleted_at=_WHEN)
    parent = build_bare_item(db, item_kind_id=kind_id, split_at=_WHEN)
    build_bare_item(db, item_kind_id=kind_id, parent_item_id=parent.id)
    build_bare_item(
        db, item_kind_id=kind_id, parent_item_id=parent.id, deleted_at=_WHEN
    )


def _searched_ids(db: Session, spec: ViewSpec) -> set[int]:
    """Every id the view's search returns with its default parameters."""
    rows, total = search(db, spec, params={}, limit=1000)
    assert len(rows) == total
    return {int(row["id"]) for row in rows}


def _live_ids(db: Session, kind_codes: set[str], *, negate: bool = False) -> set[int]:
    """The ids `live_item()` selects among items of `kind_codes` (or not of)."""
    in_kinds = ItemKind.code.in_(kind_codes)
    stmt = (
        select(InventoryItem.id)
        .join(ItemKind, ItemKind.id == InventoryItem.item_kind_id)
        .where(live_item(), ~in_kinds if negate else in_kinds)
    )
    return set(db.execute(stmt).scalars())


def test_the_search_views_and_live_item_select_the_same_rows(db: Session) -> None:
    for kind_code in ("coin", "currency", "bullion"):
        _build_every_state(db, kind_code)

    coin_ids = _searched_ids(db, COIN_VIEW)
    currency_ids = _searched_ids(db, CURRENCY_VIEW)

    # A fixture that built nothing live would make the equality vacuous.
    assert len(coin_ids) == 4  # coin and bullion: a live item and a child each
    assert len(currency_ids) == 2
    assert coin_ids == _live_ids(db, {"currency"}, negate=True)
    assert currency_ids == _live_ids(db, {"currency"})
