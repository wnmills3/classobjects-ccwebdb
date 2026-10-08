"""The demo catalog obeys every offer's rules and stays out of a collection.

`seed.seed` is only ever called here **with this test's session**: called
with none it opens its own `SessionLocal()` session bound to
`settings.database_url` -- the real database, not this test's rolled-back
transaction -- and would seed whatever database the environment points at.
"""

from __future__ import annotations

import pytest
from app.config import settings
from app.models import InventoryItem, Listing, OfferClaim, User
from app.seed import SAMPLE_CATALOG, _build, seed
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.builders import ItemFactory, build_purchase_order

_DEMO_TITLES = [row["title"] for row in SAMPLE_CATALOG]


def _demo_items(db: Session) -> int:
    """How many items carry a demo catalog title."""
    found = db.scalar(
        select(func.count())
        .select_from(InventoryItem)
        .where(InventoryItem.source_title.in_(_DEMO_TITLES))
    )
    return found or 0


def _first_admin(db: Session) -> User | None:
    """The account `seed` creates, if it is there."""
    return db.scalar(select(User).where(User.email == settings.first_admin_email))


def test_a_database_holding_an_item_is_not_seeded(
    db: Session, make_item: ItemFactory
) -> None:
    """Demo coins listed in a real shop are coins nobody can ship.

    And the account `seed` makes has a password published in the
    repository's own documents, so neither is written.
    """
    make_item(title="A coin somebody owns")

    with pytest.raises(SystemExit, match="already holds"):
        seed(db)

    assert _demo_items(db) == 0
    assert _first_admin(db) is None


def test_a_database_holding_a_purchase_is_not_seeded(db: Session) -> None:
    build_purchase_order(db, vendor_name="a-real-vendor.example")

    with pytest.raises(SystemExit, match="already holds"):
        seed(db)

    assert _demo_items(db) == 0
    assert _first_admin(db) is None


def test_an_empty_database_is_seeded_and_seeding_it_again_adds_nothing(
    db: Session,
) -> None:
    """Its own demo items are not a collection: a second run is not refused."""
    seed(db)

    assert _demo_items(db) == len(SAMPLE_CATALOG)
    admin = _first_admin(db)
    assert admin is not None

    seed(db)

    assert _demo_items(db) == len(SAMPLE_CATALOG)
    assert db.scalar(select(func.count()).select_from(InventoryItem)) == len(
        SAMPLE_CATALOG
    )


def test_a_database_in_use_is_seeded_only_when_told_to(
    db: Session, make_item: ItemFactory
) -> None:
    make_item(title="A coin somebody owns")

    seed(db, into_existing=True)

    assert _demo_items(db) == len(SAMPLE_CATALOG)


def test_every_seeded_listing_has_a_claim(db: Session) -> None:
    """The invariant holds with no exceptions, dev data included."""
    for row in SAMPLE_CATALOG:
        _build(db, row)
    db.flush()

    listings = db.scalars(select(Listing)).all()
    assert listings
    for listing in listings:
        claims = db.scalars(
            select(OfferClaim).where(OfferClaim.listing_id == listing.id)
        ).all()
        assert claims, f"listing {listing.id} has no claim"
