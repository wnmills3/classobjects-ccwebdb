"""Purchase orders must not be fabricated from missing data.

The original loader keyed on ``(vendor_id, order_number or "")``, so twelve
"I do not know the order number" became twelve assertions that these were all
one order -- 2,644 items and 42% of the cost basis. These pin the corrected
behaviour.
"""

from __future__ import annotations

import pytest
from app.order_repair import identify


@pytest.mark.parametrize(
    ("link", "expected"),
    [
        # Auction houses put their own lot id in the URL. One lot, won once.
        ("https://hibid.com/lot/226778844/1886-morgan", ("226778844", True)),
        (
            "https://www.liveauctioneers.com/item/194045321_1792-half-disme",
            ("194045321", True),
        ),
        (
            "https://www.proxibid.com/lotinformation/91896928/1928p-gold",
            ("91896928", True),
        ),
        # Etsy exposes an actual purchase receipt.
        ("https://www.etsy.com/your/purchases/3458000561?ref=x", ("3458000561", True)),
    ],
)
def test_a_vendor_lot_id_is_an_order_number(
    link: str, expected: tuple[str, bool]
) -> None:
    assert identify(link) == expected


def test_an_ebay_item_number_is_not_an_order_number() -> None:
    """It names a listing, not a purchase.

    Two separate purchases of one listing share the number, so writing it into
    `order_number` would invite someone to look it up as an order and find
    something else. It still groups the rows -- the second element says it may
    not be presented as an order number.
    """
    identifier, is_order_number = identify("https://www.ebay.com/itm/306947694169")
    assert identifier == "306947694169"
    assert is_order_number is False


def test_an_unrecognised_link_yields_nothing() -> None:
    """No identifier means no order, rather than a group invented from a date.

    Grouping by date would assert that two unrelated purchases from one vendor
    on one day were a single transaction -- the same error in a smaller form.
    """
    assert identify("https://example.com/some/page") is None
    assert identify("") is None
    assert identify(None) is None


def test_an_etsy_listing_is_not_a_purchase() -> None:
    """`/listing/` is the item on sale; `/your/purchases/` is the receipt."""
    assert identify("https://www.etsy.com/listing/1349941987/us-silver") is None
