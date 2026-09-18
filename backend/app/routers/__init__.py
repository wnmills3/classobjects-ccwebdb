"""HTTP routers, one module per resource."""

from . import (
    auth,
    catalog,
    images,
    inventory,
    offers,
    orders,
    reference,
    sales_venues,
)

__all__ = [
    "auth",
    "catalog",
    "images",
    "inventory",
    "offers",
    "orders",
    "reference",
    "sales_venues",
]
