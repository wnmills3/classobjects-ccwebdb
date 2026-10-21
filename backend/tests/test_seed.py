"""The demo catalog obeys every offer's rules and stays out of a collection.

`seed.seed` is only ever called here **with this test's session**: called
with none it opens its own `SessionLocal()` session bound to
`settings.database_url` -- the real database, not this test's rolled-back
transaction -- and would seed whatever database the environment points at.
"""

from __future__ import annotations

import pytest
from app.config import settings
from app.models import InventoryItem, Listing, OfferClaim, User, UserRole
from app.security import hash_password
from app.seed import SAMPLE_CATALOG, _build, seed
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.builders import ItemFactory, build_purchase_order

_DEMO_TITLES = [row["title"] for row in SAMPLE_CATALOG]


@pytest.fixture(autouse=True)
def _a_strong_admin_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whatever .env holds, the seed here is given a password it accepts."""
    monkeypatch.setattr(settings, "first_admin_password", "a-long-unpublished-one")


@pytest.mark.parametrize(
    ("password", "problem"),
    [
        ("", "shorter than 12"),
        ("elevenchars", "shorter than 12"),
        ("adminpassword", "published"),
    ],
)
def test_the_first_administrator_needs_a_strong_password(
    db: Session, monkeypatch: pytest.MonkeyPatch, password: str, problem: str
) -> None:
    """A missing, short or published password makes no manager at all."""
    monkeypatch.setattr(settings, "first_admin_password", password)
    with pytest.raises(SystemExit, match=problem):
        seed(db)
    assert _first_admin(db) is None
    assert _demo_items(db) == 0


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


def test_the_first_administrator_is_stored_as_sign_in_looks_it_up(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The address normalised as registration would: domain in lower case."""
    monkeypatch.setattr(settings, "first_admin_email", "Owner@Example.COM")
    seed(db)

    stored = db.scalars(select(User.email).where(User.role == UserRole.manager)).all()
    assert stored == ["Owner@example.com"]


def test_a_first_administrator_in_another_case_is_not_made_twice(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An account already holding the address, in any case, is the admin."""
    db.add(
        User(
            email="owner@example.com",
            full_name="Owner",
            hashed_password=hash_password("an-existing-password"),
            role=UserRole.manager,
        )
    )
    db.commit()
    monkeypatch.setattr(settings, "first_admin_email", "OWNER@example.com")

    seed(db)

    holders = db.scalar(
        select(func.count())
        .select_from(User)
        .where(func.lower(User.email) == "owner@example.com")
    )
    assert holders == 1


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
