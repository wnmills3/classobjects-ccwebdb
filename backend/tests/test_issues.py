"""Anomalies as named, kind-aware checks rather than generic field filters.

`issue=no_grade` means *a coin or banknote with no grade*, because bullion has
no grade by nature and 712 rounds have no weight either, so a generic
grade=null would bury 2,965 real cases under rounds that will never have one.
The domain knowledge belongs in the check, defined once, rather than in the
head of whoever types the filter.
"""

from __future__ import annotations

import re
from decimal import Decimal

from app.inventory_search import VIEWS
from app.models import ItemKind
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.test_schema import code_id, make_item

#: Pulls a `k.code = 'x'` or `k.code <> 'x'` constraint out of a predicate,
#: if it states one at all. Both a view's own WHERE and an issue's SQL are
#: built from these, and the only structural contradiction this test can
#: cheaply see is two constraints on the same column that no row can satisfy
#: together.
_KIND_CONSTRAINT = re.compile(r"k\.code\s*(=|<>)\s*'([^']+)'")


def _kind_constraint(sql: str) -> tuple[bool, str] | None:
    match = _KIND_CONSTRAINT.search(sql)
    if match is None:
        return None
    return match.group(1) == "=", match.group(2)


def search(client: TestClient, headers: dict[str, str], query: str) -> dict:
    return client.get(f"/api/inventory/coins/search?{query}", headers=headers).json()


def test_a_named_check_finds_only_its_own_anomaly(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    missing = make_item(db, year_start=None)
    make_item(db, year_start=1881)

    rows = search(client, admin_headers, "issue=no_year")["rows"]
    assert [r["id"] for r in rows] == [missing.id]


def test_no_grade_ignores_bullion(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A round will never have a grade.

    Counting it as an anomaly buries the 2,965 coins that should have one.
    """
    coin = make_item(db, grade_id=None)
    make_item(db, grade_id=None, item_kind_id=code_id(db, ItemKind, "bullion"))

    rows = search(client, admin_headers, "issue=no_grade")["rows"]
    assert [r["id"] for r in rows] == [coin.id]


def test_no_weight_applies_only_to_bullion(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The mirror image: a Morgan's weight is not the interesting gap."""
    round_ = make_item(
        db, fine_weight_ozt=None, item_kind_id=code_id(db, ItemKind, "bullion")
    )
    make_item(db, fine_weight_ozt=None)

    rows = search(client, admin_headers, "issue=no_weight_bullion")["rows"]
    assert [r["id"] for r in rows] == [round_.id]


def test_zero_cost_catches_null_and_zero_alike(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    zero = make_item(db, item_cost=Decimal("0.00"))
    make_item(db, item_cost=Decimal("19.99"))

    rows = search(client, admin_headers, "issue=zero_cost")["rows"]
    assert [r["id"] for r in rows] == [zero.id]


def test_unreviewed_is_the_default_state(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Absent means unconfirmed, which is right for every imported item."""
    item = make_item(db)
    assert search(client, admin_headers, "issue=unreviewed")["total"] == 1

    client.post(
        f"/api/inventory/{item.id}/reviewed",
        json={"fields": ["grade_id"]},
        headers=admin_headers,
    )
    assert search(client, admin_headers, "issue=unreviewed")["total"] == 0


def test_a_near_duplicate_serial_is_found_within_one_order(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Exact matching misses a dropped or duplicated character.

    Scoped to one purchase order because that is where mistranscriptions
    cluster, and because two unrelated notes one character apart are two
    unrelated notes.

    Edit distance alone is not enough: a consecutive run of serials --
    exactly how star notes are deliberately bought and stored -- differs from
    its neighbour by edit distance 1 as the normal case, so the check also
    requires the two serials to differ in length. That is what a dropped or
    duplicated character does and a consecutive run does not.
    """
    from app.models import CurrencyDetail, PurchaseOrder, Vendor

    vendor = Vendor(name="test-vendor")
    db.add(vendor)
    db.flush()
    order = PurchaseOrder(vendor_id=vendor.id, order_number="X1")
    db.add(order)
    db.flush()

    kind = code_id(db, ItemKind, "currency")
    pair = [
        make_item(db, item_kind_id=kind, purchase_order_id=order.id) for _ in range(2)
    ]
    consecutive = [
        make_item(db, item_kind_id=kind, purchase_order_id=order.id) for _ in range(2)
    ]
    apart = make_item(db, item_kind_id=kind, purchase_order_id=order.id)
    db.add_all(
        [
            # A dropped '0': ten characters against nine, one edit apart.
            CurrencyDetail(inventory_item_id=pair[0].id, serial_number="B08084501A"),
            CurrencyDetail(inventory_item_id=pair[1].id, serial_number="B0808451A"),
            # A consecutive run, same length throughout -- the shape of a
            # deliberate purchase of sequential star notes, not a
            # mistranscription, and must not be flagged.
            CurrencyDetail(
                inventory_item_id=consecutive[0].id, serial_number="G03986160"
            ),
            CurrencyDetail(
                inventory_item_id=consecutive[1].id, serial_number="G03986161"
            ),
            CurrencyDetail(inventory_item_id=apart.id, serial_number="Z99999999A"),
        ]
    )
    db.commit()

    body = client.get(
        "/api/inventory/currency/search?issue=near_duplicate_serial",
        headers=admin_headers,
    ).json()

    ids = sorted(r["id"] for r in body["rows"])
    assert ids == sorted(i.id for i in pair)
    assert consecutive[0].id not in ids
    assert consecutive[1].id not in ids


def test_an_unknown_issue_is_refused_listing_the_known_ones(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Silently ignoring it returns the whole collection as a clean bill."""
    response = client.get(
        "/api/inventory/coins/search?issue=no_such_check", headers=admin_headers
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "no_such_check" in detail
    assert "no_year" in detail


def test_issue_counts_come_back_with_the_page(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """So the size of a job is visible before committing to it."""
    make_item(db, year_start=None)
    make_item(db, year_start=None, country_id=None)

    body = client.get(
        "/api/inventory/coins/search?facets=true", headers=admin_headers
    ).json()

    assert body["issues"]["no_year"] == 2
    assert body["issues"]["no_country"] == 1


def test_issue_counts_ignore_the_issue_filter_itself(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Otherwise every count but the selected one collapses.

    The panel stops being a way to choose the next job.
    """
    make_item(db, year_start=None)
    make_item(db, country_id=None)

    body = client.get(
        "/api/inventory/coins/search?issue=no_year&facets=true", headers=admin_headers
    ).json()

    assert body["total"] == 1
    assert body["issues"]["no_country"] == 1


def test_a_currency_only_check_is_not_offered_to_coins(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A star note check over the coin view would be a 422, not empty."""
    response = client.get(
        "/api/inventory/coins/search?issue=star_mismatch", headers=admin_headers
    )
    assert response.status_code == 422


def test_an_unclassified_item_is_reachable_and_flagged(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """An unclassified item is precisely the item someone needs to find.

    A coin view that excluded kind `unknown` would make the item unreachable
    on any screen, and `issue=kind_unknown` would read as a clean bill of
    health while never actually running.
    """
    unknown = make_item(db, item_kind_id=code_id(db, ItemKind, "unknown"))

    rows = search(client, admin_headers, "")["rows"]
    assert unknown.id in [r["id"] for r in rows]

    rows = search(client, admin_headers, "issue=kind_unknown")["rows"]
    assert [r["id"] for r in rows] == [unknown.id]


def test_every_issue_a_view_offers_can_actually_fire(db: Session) -> None:
    """A check that cannot return a row reads as a clean bill of health.

    kind_unknown shipped in both views while COIN_VIEW excluded 'unknown' and
    CURRENCY_VIEW excludes it still. The check is only meaningful in a view
    whose WHERE clause can admit a matching row.

    This does not prove every issue can fire -- only that no issue's own
    `k.code` constraint structurally contradicts its view's `k.code`
    constraint. An issue naming no `k.code` at all, or one whose
    contradiction lives in a different column, passes this test whether or
    not it can ever match a row. What it does catch is exactly the shape of
    bug that shipped: a check moved, or shared, into a view its own kind
    constraint rules out.
    """
    for spec in VIEWS.values():
        view_constraints = [
            c for clause in spec.where if (c := _kind_constraint(clause)) is not None
        ]
        for name, issue in spec.issues.items():
            issue_constraint = _kind_constraint(issue.sql)
            if issue_constraint is None:
                continue
            issue_is_eq, issue_kind = issue_constraint
            for view_is_eq, view_kind in view_constraints:
                if view_is_eq and issue_is_eq:
                    # Both pin k.code to a single value: they must agree.
                    possible = view_kind == issue_kind
                elif view_is_eq != issue_is_eq:
                    # One pins it, the other excludes a value: fine unless
                    # the excluded value is the one pinned.
                    possible = view_kind != issue_kind
                else:
                    # Both only exclude a value each -- never a structural
                    # contradiction by itself.
                    possible = True
                assert possible, (
                    f"{spec.name}.{name} requires k.code "
                    f"{'=' if issue_is_eq else '<>'} {issue_kind!r}, but "
                    f"{spec.name}'s own WHERE requires k.code "
                    f"{'=' if view_is_eq else '<>'} {view_kind!r}: no row in "
                    "this view can ever satisfy this check"
                )
