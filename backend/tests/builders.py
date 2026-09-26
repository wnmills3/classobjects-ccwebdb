"""Plain helpers that build test data, shared by more than one test module.

A module of functions, not fixtures: a test imports what it calls, and
`conftest.py` imports from here too. Nothing in this module is collected by
pytest or registers a fixture, so importing it has no side effects -- which is
why a helper two test modules need lives here rather than in one of them,
where importing it would also import that module's tests.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from decimal import Decimal

from app.classifier_defaults import classify
from app.models import (
    Auction,
    Authenticity,
    Country,
    Currency,
    Customer,
    Denomination,
    Disposition,
    InventoryItem,
    ItemKind,
    ItemStatus,
    Listing,
    NoteIssue,
    NoteType,
    PurchaseOrder,
    ReferenceMixin,
    SalesOrder,
    SalesOrderStatus,
    SalesVenue,
    SealColor,
    SignatureCombination,
    StorageForm,
    User,
    ValuationBasis,
    Vendor,
)
from app.references import require_code
from app.sales_venues import store_venue_id
from app.security import hash_password
from fastapi.testclient import TestClient
from httpx import Response
from PIL import Image as PILImage
from sqlalchemy import select
from sqlalchemy.orm import Session

#: What the `make_item` fixture (conftest.py) returns.
ItemFactory = Callable[..., InventoryItem]
#: What the `make_listing` fixture (conftest.py) returns.
ListingFactory = Callable[..., Listing]


def code_id(db: Session, model: type[ReferenceMixin], code: str) -> int:
    """The id of the vocabulary row with this code; raises if there is none."""
    return db.execute(select(model.id).where(model.code == code)).scalar_one()


def usd_id(db: Session) -> int:
    """The id of the US dollar, which every listing needs."""
    return require_code(db, Currency, "USD", "currency")


def build_bare_item(db: Session, **overrides: object) -> InventoryItem:
    """An ordinary, fully-attributed coin, built from column values alone.

    Every NOT NULL classifier is filled in, plus a year and a country, so a
    test that wants a gap has to ask for one explicitly rather than getting
    it by accident. Unlike `conftest.build_item`, every override is a column
    name (``source_title``, ``item_kind_id``), no grade is set, and the item
    carries a cost of 100.00.
    """
    defaults: dict[str, object] = {
        "item_kind_id": code_id(db, ItemKind, "coin"),
        "storage_form_id": code_id(db, StorageForm, "single"),
        "authenticity_id": code_id(db, Authenticity, "unverified"),
        "status_id": code_id(db, ItemStatus, "received"),
        "disposition_id": code_id(db, Disposition, "held"),
        "valuation_basis_id": code_id(db, ValuationBasis, "numismatic"),
        "country_id": code_id(db, Country, "US"),
        "year_start": 1881,
        "item_cost": Decimal("100.00"),
        "shipping_cost": Decimal("0.00"),
    }
    defaults.update(overrides)
    item = InventoryItem(**defaults)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def priced_item(make_item: ItemFactory, title: str, cost: Decimal) -> InventoryItem:
    """An item with an exact cost basis: untaxed, so its total is its cost."""
    return make_item(title=title, item_cost=cost, tax_rate=Decimal("0"))


def set_disposition(db: Session, item: InventoryItem, code: str) -> None:
    """Move an item's disposition the way another write path would."""
    item.disposition_id = require_code(db, Disposition, code, "disposition")
    db.flush()


def build_purchase_order(
    db: Session, *, vendor_name: str, commit: bool = True, **order_fields: object
) -> PurchaseOrder:
    """A purchase order from a new vendor of this name.

    ``commit=True`` commits and refreshes the order; ``commit=False`` only
    flushes it, for a test that goes on to build more inside the same
    transaction before committing.
    """
    vendor = Vendor(name=vendor_name)
    db.add(vendor)
    db.flush()
    order = PurchaseOrder(vendor_id=vendor.id, **order_fields)
    db.add(order)
    if commit:
        db.commit()
        db.refresh(order)
    else:
        db.flush()
    return order


def build_auction(
    db: Session,
    venue: SalesVenue,
    *,
    title: str = "September Signature Sale",
    external_id: str | None = None,
) -> Auction:
    """A draft auction on this venue, with no lots yet."""
    row = Auction(sales_venue_id=venue.id, title=title, external_id=external_id)
    db.add(row)
    db.flush()
    return row


def add_note_issue(
    db: Session,
    denomination: str,
    year: int,
    note_type: str,
    seal: str,
    *,
    letter: str | None = None,
    signatures: str | None = None,
    prefix: str | None = None,
) -> None:
    """Record one note issue fact, named by vocabulary codes, and flush it."""
    db.add(
        NoteIssue(
            denomination_id=code_id(db, Denomination, denomination),
            series_year=year,
            series_letter=letter,
            note_type_id=code_id(db, NoteType, note_type),
            seal_color_id=code_id(db, SealColor, seal),
            signature_combination_id=(
                code_id(db, SignatureCombination, signatures) if signatures else None
            ),
            serial_prefix=prefix,
        )
    )
    db.flush()


def review_cases(db: Session, item: InventoryItem) -> list[tuple[str, str]]:
    """The classifier's review cases for this item, as (reason, detail) pairs."""
    return [
        (c.reason, c.detail)
        for c in classify(db).review
        if c.item_code == item.item_code
    ]


# ---------------------------------------------------------------------------
# Splitting a lot into pieces
# ---------------------------------------------------------------------------

#: A tube of four silver rounds, split evenly.
TUBE = {
    "mode": "equal",
    "pieces": [{"source_title": f"Silver Round {n}"} for n in range(1, 5)],
}


def build_split_lot(db: Session, **overrides: object) -> InventoryItem:
    """A lot of four pieces with a cost and shipping to divide between them."""
    defaults: dict[str, object] = {
        "source_title": "Tube of 4 Silver Rounds",
        "piece_count": 4,
        "item_cost": Decimal("100.00"),
        "shipping_cost": Decimal("8.00"),
    }
    defaults.update(overrides)
    return build_bare_item(db, **defaults)


def do_split(
    client: TestClient, headers: dict[str, str], item_id: int, payload: dict
) -> Response:
    """Ask the API to split this item into the pieces `payload` names."""
    return client.post(f"/api/inventory/{item_id}/split", json=payload, headers=headers)


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


def post_order(
    client: TestClient, headers: dict[str, str], listing_id: int, quantity: int
) -> Response:
    """Place a web-store order for `quantity` of one listing."""
    return client.post(
        "/api/orders",
        json={"items": [{"listing_id": listing_id, "quantity": quantity}]},
        headers=headers,
    )


def foreign_order(db: Session, email: str) -> SalesOrder:
    """An order belonging to somebody else."""
    other = User(email=email, hashed_password=hash_password("otherpassword"))
    db.add(other)
    db.flush()
    customer = Customer(user_id=other.id, display_name="Other", email=email)
    db.add(customer)
    db.flush()
    pending = db.execute(
        select(SalesOrderStatus.id).where(SalesOrderStatus.code == "pending")
    ).scalar_one()
    order = SalesOrder(
        customer_id=customer.id,
        sales_venue_id=store_venue_id(db),
        sales_order_status_id=pending,
        total_amount=Decimal("1.00"),
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


def make_jpeg(
    size: tuple[int, int] = (800, 600),
    color: tuple[int, int, int] = (180, 140, 40),
) -> bytes:
    """A plain JPEG with no metadata, as bytes."""
    buffer = io.BytesIO()
    PILImage.new("RGB", size, color).save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()
