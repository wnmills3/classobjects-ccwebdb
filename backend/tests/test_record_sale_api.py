"""The HTTP face of recording an outside sale.

`record_sale` itself is tested in `test_sales_writes.py`; what is checked
here is the API contract on top of it -- who may call it, how its refusals
(`SaleRefused` a 409, the narrower `SaleInputInvalid` a 422) map to HTTP, and
that the response shape (money as strings, a computed net) is what the
console needs.
"""

from __future__ import annotations

import httpx
import pytest
from app import offering_writes, sales_writes
from app.models import Listing, SalesOrder, User
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.conftest import item_of

#: The listing's own asking price (see `conftest.ebay_listing`) is 120.00.
#: The price recorded here is a different number on purpose: a test that
#: merely echoes the listing's asking price back cannot tell "the price the
#: platform actually sold for" from "the price it was offered at" -- exactly
#: the ambiguity this branch has been bitten by before. Gross, the one fee,
#: and the resulting net are three different numbers too, so none of the
#: three assertions below could pass by one value being swapped for another.
_PRICE = "115.00"
_FEE = "20.35"
_NET = "94.65"


def _sale_body(**overrides: object) -> dict[str, object]:
    """A sale request body, with fields a test can override piecemeal."""
    body: dict[str, object] = {
        "price": _PRICE,
        "buyer_username": "coinfan88",
        "external_order_id": "04-12345-67890",
        "fees": [{"kind": "commission", "amount": _FEE}],
    }
    body.update(overrides)
    return body


def _post(
    client: TestClient,
    listing_id: int,
    headers: dict[str, str] | None = None,
    **overrides: object,
) -> httpx.Response:
    """POST a sale for `listing_id` and hand back the response."""
    return client.post(
        f"/api/listings/{listing_id}/sale",
        json=_sale_body(**overrides),
        headers=headers or {},
    )


def _order_count(db: Session) -> int:
    """How many orders exist, to prove a refusal wrote none."""
    return db.scalar(select(func.count()).select_from(SalesOrder)) or 0


def test_recording_a_sale_returns_the_order_and_net(
    client: TestClient,
    db: Session,
    ebay_listing: Listing,
    admin_user: User,
    admin_headers: dict[str, str],
) -> None:
    """Gross, fee total and net: three different figures, none swappable."""
    response = _post(client, ebay_listing.id, admin_headers)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["total_amount"] == _PRICE
    assert body["fee_total"] == _FEE
    assert body["net_amount"] == _NET
    assert body["buyer"] == "coinfan88"
    assert body["external_order_id"] == "04-12345-67890"
    assert body["item_codes"] == [item_of(ebay_listing).item_code]
    # Who actually recorded it, not merely that someone did: an admin-shaped
    # `user` that was never wired through would leave this null or wrong
    # while every assertion above still passed.
    order = db.get(SalesOrder, body["id"])
    assert order is not None
    assert order.placed_by_id == admin_user.id


def test_an_anonymous_request_is_refused(
    client: TestClient, ebay_listing: Listing
) -> None:
    """Fees and cost basis are staff-only; this endpoint is admin-only."""
    response = _post(client, ebay_listing.id)
    assert response.status_code == 401


def test_a_customer_request_is_refused(
    client: TestClient, ebay_listing: Listing, customer_headers: dict[str, str]
) -> None:
    """A logged-in shopper is not staff either."""
    response = _post(client, ebay_listing.id, customer_headers)
    assert response.status_code == 403


def test_selling_an_ended_listing_is_a_409(
    client: TestClient,
    db: Session,
    ebay_listing: Listing,
    admin_headers: dict[str, str],
) -> None:
    """The refusal names what is in the way, and a 409 persists no order.

    Not "nothing gets written" -- the router rolls back before the 409
    leaves, so a future reordering that wrote an order and only then refused
    would still leave this count unchanged. What this actually proves is
    narrower: after a 409, no order exists.
    """
    offering_writes.end_offer(db, ebay_listing)
    db.commit()
    before = _order_count(db)
    response = _post(client, ebay_listing.id, admin_headers)
    assert response.status_code == 409
    assert "not on offer" in response.json()["detail"]
    assert _order_count(db) == before


def test_selling_an_unknown_listing_is_a_404(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """An id nothing wears is a 404, not a refusal about its state."""
    response = _post(client, 999_999_999, admin_headers)
    assert response.status_code == 404


def test_an_unknown_fee_kind_is_a_422(
    client: TestClient, ebay_listing: Listing, admin_headers: dict[str, str]
) -> None:
    """A code in the wrong vocabulary must not vanish into a default.

    `code_to_id` returns `detail` as the plain string `"Unknown fee:
    'gratuity'"`; a schema-level 422 returns a list of error objects instead.
    Asserting the status code alone cannot tell those two apart -- a renamed
    field or a schema slip that produced its own 422 would still pass.
    """
    response = _post(
        client,
        ebay_listing.id,
        admin_headers,
        fees=[{"kind": "gratuity", "amount": "1.00"}],
    )
    assert response.status_code == 422
    assert "Unknown fee" in response.json()["detail"]


def test_money_is_returned_as_strings(
    client: TestClient, ebay_listing: Listing, admin_headers: dict[str, str]
) -> None:
    """Every money field is a string on the wire, not a `float`."""
    response = _post(client, ebay_listing.id, admin_headers)
    body = response.json()
    for field in ("total_amount", "fee_total", "net_amount"):
        assert isinstance(body[field], str), f"{field} was {type(body[field])}"


def test_a_price_too_large_to_store_is_a_422_naming_the_field(
    client: TestClient, ebay_listing: Listing, admin_headers: dict[str, str]
) -> None:
    """`Numeric(12, 2)` cannot hold eleven whole digits.

    `1E+11` is the value that actually discriminates a correct bound
    (`max_digits` *and* `decimal_places`, matching the column) from a broken
    one (`max_digits` alone, which admits any number of whole digits as long
    as the total including any fraction stays under twelve -- `1E+11` and
    `999999999999` both pass it, then overflow `Numeric(12, 2)` in Postgres
    as an uncaught `DataError`, a 500). An absurdity like `1E+30` would pass
    against either bound and prove nothing about which one is in place.
    """
    response = _post(client, ebay_listing.id, admin_headers, price="1E+11")
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert any(err["loc"][-1] == "price" for err in detail), detail


def test_a_nan_fee_amount_is_a_422_naming_the_field(
    client: TestClient, ebay_listing: Listing, admin_headers: dict[str, str]
) -> None:
    """A non-finite fee is the other shape of the same `quantize` crash."""
    response = _post(
        client,
        ebay_listing.id,
        admin_headers,
        fees=[{"kind": "commission", "amount": "NaN"}],
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert any(err["loc"][-1] == "amount" for err in detail), detail


def test_a_negative_price_is_a_422_naming_the_field(
    client: TestClient, ebay_listing: Listing, admin_headers: dict[str, str]
) -> None:
    """A negative price is bad input, not a conflict -- refused at the door.

    Unguarded, this reaches `place_order`'s flush and violates
    `ck_sales_order_total_non_negative` as an uncaught `IntegrityError` -- a
    500, after `record_sale` has already written the order row.
    """
    response = _post(client, ebay_listing.id, admin_headers, price="-115.00")
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert any(err["loc"][-1] == "price" for err in detail), detail


def test_a_negative_fee_amount_is_a_422_naming_the_field(
    client: TestClient, ebay_listing: Listing, admin_headers: dict[str, str]
) -> None:
    """The schema's own sign bound, not `record_sale`'s, is what fires here."""
    response = _post(
        client,
        ebay_listing.id,
        admin_headers,
        fees=[{"kind": "commission", "amount": "-1.00"}],
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert any(err["loc"][-1] == "amount" for err in detail), detail


def test_a_sub_cent_fee_amount_is_a_422_naming_the_field(
    client: TestClient, ebay_listing: Listing, admin_headers: dict[str, str]
) -> None:
    """`decimal_places=2` on the schema, not `record_sale`'s own check, fires.

    `record_sale` still refuses a sub-cent fee itself -- as `SaleInputInvalid`
    -- for phase-4 auction settlement, a future caller this schema will not
    stand in front of. From this endpoint, though, a third decimal place
    never reaches it: the schema is stricter and rejects the request first,
    as a 422 naming the field, not the 409 an earlier version of this test
    wrongly expected.
    """
    response = _post(
        client,
        ebay_listing.id,
        admin_headers,
        fees=[{"kind": "commission", "amount": "20.355"}],
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert any(err["loc"][-1] == "amount" for err in detail), detail


def test_sale_input_invalid_from_record_sale_is_a_422_not_a_409(
    client: TestClient,
    ebay_listing: Listing,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The router's `except` order, not the schema, is what this pins.

    `SaleInputInvalid` **is** a `SaleRefused` -- that is the whole point of
    the subclass -- so the router's `except SaleRefused` clause would also
    match it if it came first, and a real request can no longer reach
    `record_sale`'s own `SaleInputInvalid` checks at all (the schema tests
    above already refuse a negative or sub-cent amount before this body
    runs). Patching `record_sale` to raise it directly is what isolates the
    router's ordering from the schema: with the two `except` clauses
    swapped, this test fails with a 409, silently reporting the wrong status
    the day phase-4 auction settlement calls `record_sale` directly, without
    this schema in front of it.
    """

    def _raise_invalid(*args: object, **kwargs: object) -> SalesOrder:
        raise sales_writes.SaleInputInvalid("deliberately malformed, for the test")

    monkeypatch.setattr(sales_writes, "record_sale", _raise_invalid)
    response = _post(client, ebay_listing.id, admin_headers)
    assert response.status_code == 422
    assert response.json()["detail"] == "deliberately malformed, for the test"


def test_a_plain_sale_refused_from_record_sale_is_still_a_409(
    client: TestClient,
    ebay_listing: Listing,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The base `SaleRefused` -- a genuine conflict -- still maps to 409.

    The counterpart to the test above: proves the router did not simply
    route everything to 422 to pass it.
    """

    def _raise_refused(*args: object, **kwargs: object) -> SalesOrder:
        raise sales_writes.SaleRefused("deliberately conflicting, for the test")

    monkeypatch.setattr(sales_writes, "record_sale", _raise_refused)
    response = _post(client, ebay_listing.id, admin_headers)
    assert response.status_code == 409
    assert response.json()["detail"] == "deliberately conflicting, for the test"


def test_recording_a_sale_ends_the_listing(
    client: TestClient,
    db: Session,
    ebay_listing: Listing,
    admin_headers: dict[str, str],
) -> None:
    """The endpoint both creates the order and ends the listing, together."""
    ebay_listing_id = ebay_listing.id
    response = _post(client, ebay_listing_id, admin_headers)
    assert response.status_code == 201, response.text
    db.expire_all()
    reloaded = db.get(Listing, ebay_listing_id)
    assert reloaded is not None
    assert reloaded.status.value == "ended"


def test_a_recorded_outside_sale_cannot_be_cancelled(
    client: TestClient,
    db: Session,
    ebay_listing: Listing,
    admin_headers: dict[str, str],
) -> None:
    """Cancelling one would strand the item on `sold` with no way back.

    `record_sale` ended the listing, so `order_writes.return_stock` would add
    the quantity back to an **ended** listing -- whose generated `is_active`
    is false, which is exactly the condition `_after_stock_change` needs to be
    true before it moves the item off `sold`. The order would read cancelled
    while the listing carried phantom stock and the item stayed `sold`
    forever, refused by `offering_writes._refuse_sold` on every later attempt
    to offer it. The refusal is the remedy: nothing is written at all.
    """
    ebay_listing_id = ebay_listing.id
    recorded = _post(client, ebay_listing_id, admin_headers)
    assert recorded.status_code == 201, recorded.text
    order_id = recorded.json()["id"]

    response = client.patch(
        f"/api/orders/{order_id}",
        json={"status": "cancelled"},
        headers=admin_headers,
    )
    assert response.status_code == 409, response.text
    assert "eBay" in response.json()["detail"]
    assert "cannot be cancelled" in response.json()["detail"]

    # Nothing moved: the sale still stands, and the item is still sold rather
    # than holding stock on a listing no one can see.
    db.expire_all()
    listing = db.get(Listing, ebay_listing_id)
    assert listing is not None
    assert listing.status.value == "ended"
    assert listing.quantity_available == 0
    assert item_of(listing).disposition.code == "sold"
    still_paid = client.get(f"/api/orders/{order_id}", headers=admin_headers)
    assert still_paid.status_code == 200
    assert still_paid.json()["status"] == "paid"


def test_an_auction_house_sale_can_be_cancelled_as_a_refund(
    client: TestClient,
    db: Session,
    heritage_listing: Listing,
    admin_headers: dict[str, str],
) -> None:
    """The delivered twin of the refusal above, and the case that changed.

    An auction house has already shipped for us by the time its sale is
    recorded, so `sales_writes._STATUS_BY_VENUE_KIND` starts that order at
    **delivered** -- which is in `routers.orders.SHIPPED_STATUSES`. When the
    cancel refusal was narrowed to unshipped orders, every auction-house sale
    moved from "always refused" to "always allowed" in one step, and nothing
    covered it: the branch tests a shipped *store* lot order and an unshipped
    *outside* one, never the delivered outside one that only this venue kind
    produces.

    Allowing it is right, and for exactly the reason the refusal exists.
    `return_stock` is not called for a shipped order, so nothing is added
    back to the ended listing and nothing is stranded -- the item stays
    `sold`, which is the truth after an auction house has shipped it, and
    cancelling is how the refund is recorded. The assertions below are that
    pair: the order really cancels, and no stock came back.

    The mutation that proves it: drop `previous not in SHIPPED_STATUSES |
    {"cancelled"}` from the refusal at `routers.orders` and confirm this goes
    red with a 409 naming Heritage.
    """
    heritage_listing_id = heritage_listing.id
    recorded = _post(client, heritage_listing_id, admin_headers)
    assert recorded.status_code == 201, recorded.text
    order_id = recorded.json()["id"]
    # Asserted, not assumed: "delivered" is the whole premise -- a sale that
    # started `paid` would be refused below for an ordinary reason and this
    # test would pass without ever reaching the case it is named for.
    started = client.get(f"/api/orders/{order_id}", headers=admin_headers)
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "delivered"

    response = client.patch(
        f"/api/orders/{order_id}",
        json={"status": "cancelled"},
        headers=admin_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    # No stock returned, which is what made this safe to allow: the listing
    # the sale ended stays ended and empty, and the coin stays sold.
    db.expire_all()
    listing = db.get(Listing, heritage_listing_id)
    assert listing is not None
    assert listing.status.value == "ended"
    assert listing.quantity_available == 0
    assert item_of(listing).disposition.code == "sold"
    current = client.get(f"/api/orders/{order_id}", headers=admin_headers)
    assert current.status_code == 200
    assert current.json()["status"] == "cancelled"
