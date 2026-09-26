"""The buyer behind a sale on an outside platform.

A store customer is a login. A buyer on eBay or Whatnot is a username on that
platform and nothing more, and an auction house may not name the buyer at all
-- so each platform has one standing "undisclosed buyer" to hang those sales
on. Both are `customer` rows, so orders, snapshots and history work the same
way whatever the sale was.

Uniqueness is enforced by the two partial indexes on `customer`
(`uq_customer_venue_username`, case-insensitive, and
`uq_customer_venue_undisclosed`), not by the lookup below: this function's
SELECT-then-INSERT is inherently racy -- two concurrent sales to the same new
buyer would both find nothing and both insert -- and it is the database
indexes, not application-level locking, that make that safe.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Customer, SalesVenue

__all__ = ["buyer_name", "venue_buyer"]


def buyer_name(username: str | None) -> str | None:
    """The username as the owner typed it, minus surrounding whitespace.

    Empty and whitespace-only both become `None`, the undisclosed buyer: an
    untouched optional form field sends `""`, not a missing field, and
    `venue_username = ''` is a distinct non-null value the partial unique
    index cannot catch. Deliberately **not** case-folded -- a customer row
    carries the name the owner typed, `CoinFan88`, not a flattening of it.
    """
    stripped = username.strip() if username else None
    return stripped or None


def venue_buyer(db: Session, venue: SalesVenue, username: str | None) -> Customer:
    """The customer for `username` on `venue`, created if new.

    `username` is read through `buyer_name`: None, empty, or whitespace-only
    means the platform's single undisclosed buyer, which auction houses that
    do not name buyers sell to. Otherwise a second, indistinguishable-looking
    "undisclosed buyer" row would appear beside the real one.

    Matching ignores case and surrounding whitespace: platforms display the
    same account as `CoinFan88`, `coinfan88`, and ` coinfan88 ` with a
    transcription space, and the whitespace is not part of the account name.
    See the module docstring for why this lookup does not need to be
    race-safe on its own.
    """
    stored = buyer_name(username)
    query = select(Customer).where(Customer.sales_venue_id == venue.id)
    if stored is None:
        query = query.where(Customer.venue_username.is_(None))
    else:
        query = query.where(func.lower(Customer.venue_username) == stored.lower())
    found = db.scalar(query)
    if found is not None:
        return found
    customer = Customer(
        display_name=stored or f"Undisclosed buyer ({venue.name})",
        sales_venue_id=venue.id,
        venue_username=stored,
    )
    db.add(customer)
    db.flush()
    return customer
