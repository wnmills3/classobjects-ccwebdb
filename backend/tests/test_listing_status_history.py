"""Every status a listing has had (`ListingStatusHistory`).

`offering_writes` is the only writer of `listing.status`, so it is the only
writer of this history too, in the same flush. These tests pin the rows each
transition leaves -- offered, paused, resumed, withdrawn, sold -- and prove
the suite-wide check (`conftest.check_listing_history_invariant`) catches a
status written around it.
"""

from __future__ import annotations

import pytest
from app import offering_writes
from app.models import Listing, ListingStatus, ListingStatusHistory
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.conftest import HistoryInvariantViolation, check_listing_history_invariant

Row = tuple[ListingStatus | None, ListingStatus, str | None]


def history(db: Session, listing: Listing) -> list[Row]:
    """The listing's history rows, oldest first, as (from, to, note)."""
    rows = db.scalars(
        select(ListingStatusHistory)
        .where(ListingStatusHistory.listing_id == listing.id)
        .order_by(ListingStatusHistory.id)
    ).all()
    return [(row.from_status, row.to_status, row.note) for row in rows]


def test_an_offer_opens_its_history(db: Session, ebay_listing: Listing) -> None:
    assert history(db, ebay_listing) == [(None, ListingStatus.active, "offered")]


def test_offering_elsewhere_records_the_pause_and_why(
    db: Session, stored_then_ebay: tuple[Listing, Listing]
) -> None:
    store, ebay = stored_then_ebay

    assert history(db, store) == [
        (ListingStatus.active, ListingStatus.paused, f"paused for listing #{ebay.id}")
    ]


def test_a_withdrawal_ends_one_and_resumes_the_other(
    db: Session, stored_then_ebay: tuple[Listing, Listing]
) -> None:
    store, ebay = stored_then_ebay

    offering_writes.end_offer(db, ebay)

    assert history(db, ebay) == [
        (None, ListingStatus.active, "offered"),
        (ListingStatus.active, ListingStatus.ended, "withdrawn"),
    ]
    assert history(db, store)[-1] == (
        ListingStatus.paused,
        ListingStatus.active,
        f"resumed: listing #{ebay.id} ended",
    )


def test_a_sale_is_told_apart_from_a_withdrawal(
    db: Session, stored_then_ebay: tuple[Listing, Listing]
) -> None:
    """The distinction the status alone cannot carry, and settlement needs."""
    store, ebay = stored_then_ebay

    offering_writes.end_offer(db, ebay, sold=True)

    assert history(db, ebay)[-1] == (
        ListingStatus.active,
        ListingStatus.ended,
        "sold",
    )
    assert history(db, store)[-1] == (
        ListingStatus.paused,
        ListingStatus.ended,
        f"ended: sold through listing #{ebay.id}",
    )


def test_ending_an_ended_listing_adds_no_row(
    db: Session, ebay_listing: Listing
) -> None:
    """`_end`'s early return keeps a double-click out of the record too."""
    offering_writes.end_offer(db, ebay_listing)
    offering_writes.end_offer(db, ebay_listing)

    assert len(history(db, ebay_listing)) == 2


def test_the_invariant_catches_a_status_written_around_the_writer(
    db: Session, ebay_listing: Listing
) -> None:
    """The mutation this suite-wide check exists for: a second writer."""
    ebay_listing.status = ListingStatus.ended
    db.flush()

    with pytest.raises(HistoryInvariantViolation):
        check_listing_history_invariant(db)

    # Put it back, so the autouse check at teardown grades a sound database.
    ebay_listing.status = ListingStatus.active
    db.flush()
