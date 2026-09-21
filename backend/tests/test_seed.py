"""The demo catalogue obeys the same rules as every other offer.

`_build` is exercised directly, the same way `test_item_years.py` already
does, rather than through `seed.seed()`: that function opens its own
`SessionLocal()` session bound to `settings.database_url` -- the real
database, not this test's rolled-back transaction -- so calling it from a
test would seed whatever database the environment happens to point at.
"""

from __future__ import annotations

from app.models import Listing, OfferClaim
from app.seed import SAMPLE_CATALOG, _build
from sqlalchemy import select
from sqlalchemy.orm import Session


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
