"""The HTTP face of recording an outside sale.

`record_sale` itself is tested in `test_sales_writes.py`; what is checked
here is the API contract on top of it -- who may call it, how its refusals
map to HTTP, and that the response shape (money as strings, a computed net)
is what the console needs.
"""

from __future__ import annotations

import httpx
from app import offering_writes
from app.models import Listing
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

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


def test_recording_a_sale_returns_the_order_and_net(
    client: TestClient, ebay_listing: Listing, admin_headers: dict[str, str]
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
    assert body["item_codes"] == [ebay_listing.inventory_item.item_code]


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
    """The refusal names what is in the way: the listing is not on offer."""
    offering_writes.end_offer(db, ebay_listing)
    db.commit()
    response = _post(client, ebay_listing.id, admin_headers)
    assert response.status_code == 409
    assert "not on offer" in response.json()["detail"]


def test_selling_an_unknown_listing_is_a_404(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """An id nothing wears is a 404, not a refusal about its state."""
    response = _post(client, 999_999_999, admin_headers)
    assert response.status_code == 404


def test_an_unknown_fee_kind_is_a_422(
    client: TestClient, ebay_listing: Listing, admin_headers: dict[str, str]
) -> None:
    """A code in the wrong vocabulary must not vanish into a default."""
    response = _post(
        client,
        ebay_listing.id,
        admin_headers,
        fees=[{"kind": "gratuity", "amount": "1.00"}],
    )
    assert response.status_code == 422


def test_money_is_returned_as_strings(
    client: TestClient, ebay_listing: Listing, admin_headers: dict[str, str]
) -> None:
    """FastAPI's encoder turns a `Decimal` in a plain dict into a float."""
    response = _post(client, ebay_listing.id, admin_headers)
    body = response.json()
    for field in ("total_amount", "fee_total", "net_amount"):
        assert isinstance(body[field], str), f"{field} was {type(body[field])}"


def test_a_price_too_large_to_store_is_a_422_naming_the_field(
    client: TestClient, ebay_listing: Listing, admin_headers: dict[str, str]
) -> None:
    """`Numeric(12, 2)` cannot hold this; `record_sale` must never see it.

    Unguarded, this magnitude reaches `record_sale`'s sub-cent check, which
    calls `Decimal.quantize` and raises a bare `decimal.InvalidOperation` --
    a 500, not a refusal. The request schema must reject it first.
    """
    response = _post(client, ebay_listing.id, admin_headers, price="1E+30")
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


def test_a_sub_cent_fee_still_reaches_record_sale_as_a_409(
    client: TestClient, ebay_listing: Listing, admin_headers: dict[str, str]
) -> None:
    """The magnitude bound must not also swallow the sub-cent refusal.

    `record_sale` refuses a sub-cent fee itself, as `SaleRefused` -- a 409
    naming the problem, not a 422 from the schema. If the request schema
    ever added `decimal_places=2` on top of its magnitude bound, this case
    would wrongly become a 422 and the assertion below would catch it.
    """
    response = _post(
        client,
        ebay_listing.id,
        admin_headers,
        fees=[{"kind": "commission", "amount": "20.355"}],
    )
    assert response.status_code == 409
    assert "cent" in response.json()["detail"]


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
