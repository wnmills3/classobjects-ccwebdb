"""Finding or creating the buyer behind an outside sale."""

from __future__ import annotations

from app.buyers import venue_buyer
from app.models import SalesVenue
from sqlalchemy.orm import Session


def test_same_username_returns_the_same_customer(
    db: Session, ebay_venue: SalesVenue
) -> None:
    """A repeat buyer is one customer record, not one per sale."""
    first = venue_buyer(db, ebay_venue, "coinfan88")
    second = venue_buyer(db, ebay_venue, "coinfan88")
    assert first.id == second.id


def test_username_is_matched_case_insensitively(
    db: Session, ebay_venue: SalesVenue
) -> None:
    """Platforms display usernames inconsistently; the person is the same."""
    first = venue_buyer(db, ebay_venue, "CoinFan88")
    second = venue_buyer(db, ebay_venue, "coinfan88")
    assert first.id == second.id


def test_the_same_name_on_two_platforms_is_two_people(
    db: Session, ebay_venue: SalesVenue, whatnot_venue: SalesVenue
) -> None:
    """`coinfan88` on eBay and on Whatnot are not known to be the same buyer."""
    assert (
        venue_buyer(db, ebay_venue, "coinfan88").id
        != venue_buyer(db, whatnot_venue, "coinfan88").id
    )


def test_undisclosed_buyer_is_one_per_platform(
    db: Session, heritage_venue: SalesVenue
) -> None:
    """An auction house that names no buyer gets one standing record."""
    first = venue_buyer(db, heritage_venue, None)
    second = venue_buyer(db, heritage_venue, None)
    assert first.id == second.id
    assert first.display_name == "Undisclosed buyer (Heritage)"
