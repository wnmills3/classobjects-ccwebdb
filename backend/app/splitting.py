"""Breaking a lot into the pieces it is made of.

A tube of twenty rounds, a roll of cents, a mint set: bought as one thing,
often sold as many. Splitting creates one item per piece and divides the lot's
cost between them, so each piece carries a cost basis that traces to the
purchase it actually came from.

**Two ways to divide the money, and the choice is not cosmetic.**

`equal` gives every piece the same share. Right when the pieces are
interchangeable -- twenty identical one-ounce rounds.

`relative` divides in proportion to a value supplied per piece. Right when they
are not. A 1964 mint set contains a cent and a half dollar; charging them the
same cost basis would make the cent look like a catastrophic loss and the half
a windfall, and both figures would be wrong on a return.

The relative basis is the caller's to choose -- face value, melt value, a
catalogue price. This module takes the numbers and does not opine on where they
came from, except to record them. It is worth being explicit that proportional
value is a convention, not a measurement: it does not know that one coin in the
set is the key date and the rest are common. It is a defensible way to divide a
cost, not a valuation.

**The parent is kept, never deleted.** It holds the purchase order, the price
actually paid and the item code a receipt refers to. It is marked `split_at`
and must then be excluded from anything that counts inventory or money --
otherwise the lot and its pieces are both counted, and the collection appears
to have cost twice what it did.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .allocation import allocate
from .models import (
    Disposition,
    InventoryItem,
    ItemStatusHistory,
    Listing,
    ProvenanceSource,
    SalesOrderItem,
)

__all__ = ["EQUAL", "RELATIVE", "SplitError", "SplitPiece", "split_item"]

EQUAL = "equal"
RELATIVE = "relative"
MODES = (EQUAL, RELATIVE)

#: Columns a piece inherits from the lot unless the caller overrides them.
#: Weight is per piece already, so it carries over unchanged -- each round out
#: of a tube of one-ounce rounds still weighs an ounce.
INHERITED = (
    "item_kind_id",
    "denomination_id",
    "bullion_form_id",
    "set_form_id",
    "country_id",
    "year_start",
    "year_end",
    "grade_id",
    "grade_designation_id",
    "grading_service_id",
    "authenticity_id",
    "error_type_id",
    "status_id",
    "storage_location_id",
    "valuation_basis_id",
    "composition_id",
    "metal_id",
    "fineness",
    "gross_weight_ozt",
    "fine_weight_ozt",
    "weight_raw",
    "purchase_order_id",
    "tax_rate",
    "description",
)


class SplitError(ValueError):
    """The lot cannot be split as asked."""


@dataclass
class SplitPiece:
    """One piece to create."""

    title: str
    storage_quantity: int = 1
    #: The value of ONE piece, on whatever basis the caller chose. Only used
    #: in `relative` mode.
    relative_value: Decimal | None = None
    #: Resolved column values overriding what the piece would inherit.
    overrides: dict[str, Any] = field(default_factory=dict)


def _weights(pieces: list[SplitPiece], mode: str) -> list[Decimal]:
    """How much of the cost each piece should carry.

    Weight is per-piece value times the number of pieces, so a child holding
    three coins carries three coins' worth. In `equal` mode every piece is
    worth 1, which reduces the weight to simply the count -- so an equal split
    is per *piece*, not per *row*.
    """
    if mode == EQUAL:
        return [Decimal(piece.storage_quantity) for piece in pieces]

    missing = [p.title for p in pieces if p.relative_value is None]
    if missing:
        raise SplitError(
            "relative mode needs a relative_value for every piece; missing "
            f"for: {missing}"
        )
    if any(p.relative_value < 0 for p in pieces):
        raise SplitError("relative_value cannot be negative")
    return [
        Decimal(piece.relative_value) * piece.storage_quantity for piece in pieces
    ]


def split_item(
    db: Session,
    parent: InventoryItem,
    pieces: list[SplitPiece],
    mode: str = EQUAL,
) -> list[InventoryItem]:
    """Break `parent` into `pieces`, dividing its cost between them."""
    if mode not in MODES:
        raise SplitError(f"unknown mode {mode!r}; expected one of {MODES}")
    if len(pieces) < 2:
        raise SplitError("splitting into fewer than two pieces does nothing")

    # Lock the lot before deciding whether it can be split.
    #
    # Without this, two people splitting the same tube at the same moment both
    # read split_at as NULL, both pass the check, and the lot is broken up
    # twice -- eight pieces from four, and the cost basis allocated twice over.
    # This is a check-then-act on a single row, which is what SELECT ... FOR
    # UPDATE is for: the second caller waits, then sees the first one's work.
    #
    # Pessimistic here rather than optimistic because the whole operation is
    # one short transaction with nothing for a human to merge -- unlike an
    # edit form, which is held open for minutes.
    # refresh() rather than a fresh select(): a select returns the object
    # already in the identity map *without* re-reading its columns, so the
    # lock would be taken and then the decision made on the stale values this
    # session loaded before waiting for it -- which is precisely the race.
    db.refresh(parent, with_for_update=True)

    if parent.split_at is not None:
        raise SplitError(f"{parent.item_code} has already been split")
    if parent.parent_item_id is not None:
        # Allowing this would make cost basis a tree to walk rather than a
        # single hop, for no case that has come up.
        raise SplitError(
            f"{parent.item_code} is itself a piece of a lot; split the lot"
        )
    if any(quantity < 1 for quantity in (p.storage_quantity for p in pieces)):
        raise SplitError("every piece must hold at least one item")

    sold = db.scalar(
        select(SalesOrderItem.id)
        .join(Listing, Listing.id == SalesOrderItem.listing_id)
        .where(Listing.inventory_item_id == parent.id)
        .limit(1)
    )
    if sold is not None:
        # Order history points at the lot. Splitting it now would leave a sold
        # line referring to something that no longer exists as sold.
        raise SplitError(
            f"{parent.item_code} appears in an order and cannot be split"
        )

    weights = _weights(pieces, mode)
    prices = allocate(parent.price, weights)
    shipping = allocate(parent.shipping, weights)

    now = datetime.now(timezone.utc)
    held = db.scalar(select(Disposition.id).where(Disposition.code == "held"))

    children: list[InventoryItem] = []
    for piece, price, ship in zip(pieces, prices, shipping, strict=True):
        values = {column: getattr(parent, column) for column in INHERITED}
        # Packaging is inherited but routinely overridden: pieces come out of
        # a tube or a set as singles, whatever the lot was packaged as.
        values["storage_form_id"] = parent.storage_form_id
        values.update(piece.overrides)

        child = InventoryItem(
            **values,
            parent_item_id=parent.id,
            title=piece.title,
            storage_quantity=piece.storage_quantity,
            disposition_id=held,
            price=price,
            shipping=ship,
            source=ProvenanceSource.derived,
            attributes={
                **(parent.attributes or {}),
                "split_from": parent.item_code,
                "split_mode": mode,
                # The number the allocation actually used, kept so the
                # division can be re-checked later without guessing at what
                # basis someone had in mind.
                **(
                    {"split_relative_value": str(piece.relative_value)}
                    if piece.relative_value is not None
                    else {}
                ),
            },
        )
        db.add(child)
        db.flush()

        db.add(
            ItemStatusHistory(
                inventory_item_id=child.id,
                from_status_id=None,
                to_status_id=child.status_id,
                changed_at=now,
                note=f"split from {parent.item_code}",
            )
        )
        children.append(child)

    # The lot is no longer a thing anyone holds.
    parent.split_at = now
    for listing in db.scalars(
        select(Listing).where(Listing.inventory_item_id == parent.id)
    ):
        listing.is_active = False
        listing.ended_at = now

    db.flush()
    return children


def storage_form_override(db: Session, code: str) -> dict[str, int]:
    """Convenience for the common case: pieces come out as singles."""
    from .models import StorageForm

    single = db.scalar(select(StorageForm.id).where(StorageForm.code == code))
    return {"storage_form_id": single} if single else {}
