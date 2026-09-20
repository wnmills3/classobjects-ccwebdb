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


def test_empty_username_is_the_same_undisclosed_buyer_as_none(
    db: Session, heritage_venue: SalesVenue
) -> None:
    """An untouched optional form field sends `""`, not a missing field.

    That must not create a second, indistinguishable-looking "undisclosed
    buyer" row beside the real one: `venue_username = ''` is a distinct
    non-null value, so the partial unique index cannot catch it.
    """
    undisclosed = venue_buyer(db, heritage_venue, None)
    empty = venue_buyer(db, heritage_venue, "")
    assert empty.id == undisclosed.id


def test_whitespace_only_username_is_the_same_undisclosed_buyer_as_none(
    db: Session, heritage_venue: SalesVenue
) -> None:
    """Whitespace typed into an otherwise-empty field is still no username."""
    undisclosed = venue_buyer(db, heritage_venue, None)
    blank = venue_buyer(db, heritage_venue, "   ")
    assert blank.id == undisclosed.id


def test_surrounding_whitespace_is_not_part_of_the_username(
    db: Session, ebay_venue: SalesVenue
) -> None:
    """A transcription space around a real username is not a different buyer."""
    first = venue_buyer(db, ebay_venue, "coinfan88")
    second = venue_buyer(db, ebay_venue, " coinfan88 ")
    assert first.id == second.id
