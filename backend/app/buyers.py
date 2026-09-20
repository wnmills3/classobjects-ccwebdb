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

    `username` of None means the platform's single undisclosed buyer, which
    auction houses that do not name buyers sell to.

    Matching ignores case: platforms display the same account as `CoinFan88`
    and `coinfan88`, and two records would split one person's history. See
    the module docstring for why this lookup does not need to be race-safe
    on its own.
    """
    query = select(Customer).where(Customer.sales_venue_id == venue.id)
    if username is None:
        query = query.where(Customer.venue_username.is_(None))
    else:
        query = query.where(func.lower(Customer.venue_username) == username.lower())
    found = db.scalar(query)
    if found is not None:
        return found
    customer = Customer(
        display_name=username or f"Undisclosed buyer ({venue.name})",
        sales_venue_id=venue.id,
        venue_username=username,
    )
    db.add(customer)
    db.flush()
    return customer
