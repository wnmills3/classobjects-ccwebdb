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

__all__ = ["venue_buyer"]


def venue_buyer(db: Session, venue: SalesVenue, username: str | None) -> Customer:
    """The customer for `username` on `venue`, created if new.

    `username` of None, empty, or whitespace-only means the platform's single
    undisclosed buyer, which auction houses that do not name buyers sell to.
    An empty string is exactly what an untouched optional form field sends --
    not a missing field, an empty one -- so it must resolve the same way None
    does, or a second, indistinguishable-looking "undisclosed buyer" row
    appears beside the real one: `venue_username = ''` is a distinct non-null
    value, so the partial unique index cannot catch it.

    Matching ignores case and surrounding whitespace: platforms display the
    same account as `CoinFan88`, `coinfan88`, and ` coinfan88 ` with a
    transcription space, and the whitespace is not part of the account name.
    See the module docstring for why this lookup does not need to be
    race-safe on its own.
    """
    stored = username.strip() if username else None
    if stored == "":
        stored = None
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
