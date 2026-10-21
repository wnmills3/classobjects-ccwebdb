"""What a request body may hold, checked where it is declared (`app.schemas`).

A value the database would refuse or round, and a key the route does not
read, are refused at validation: the first would otherwise surface as a
server error or be stored as something else, the second would answer 200
for a change never made. A stored address a response offers as a link is
withheld unless it is a safe one. Nothing here opens a connection.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.schemas import (
    AddressIn,
    CustomerUpdate,
    FriedbergAttachIn,
    FriedbergNumberCreate,
    ImageLinkIn,
    ImageLinkOut,
    ImageLinkUpdate,
    ImageOut,
    InventoryItemUpdate,
    ItemCreate,
    ItemErrorIn,
    ItemErrorsRequest,
    OfferIn,
    OrderStatusUpdate,
    PurchaseOrderCreate,
    PurchaseOrderDetailOut,
    ReceiveRequest,
    ReferenceAliasIn,
    ReferenceMergeIn,
    ReferenceValueCreate,
    ReferenceValueRename,
    SalesVenueCreate,
    SalesVenueUpdate,
    SplitPieceIn,
    SplitRequest,
    UserUpdate,
    VendorCreate,
    VendorUpdate,
)
from pydantic import BaseModel, ValidationError

#: A body each schema accepts, to which one unknown key is then added.
_ACCEPTED: list[tuple[type[BaseModel], dict[str, Any]]] = [
    (OrderStatusUpdate, {"status": "paid", "version": 1}),
    (ReferenceValueCreate, {"label": "Test"}),
    (ReferenceValueRename, {"label": "Test"}),
    (ReferenceMergeIn, {"into": "DCAM"}),
    (ReferenceAliasIn, {"alias": "Test"}),
    (ImageLinkUpdate, {"is_primary": True}),
    (ImageLinkIn, {"inventory_item_id": 1}),
    (SplitPieceIn, {"source_title": "A piece"}),
    (
        SplitRequest,
        {"pieces": [{"source_title": "A"}, {"source_title": "B"}]},
    ),
    (ReceiveRequest, {"item_ids": [1], "outcome": "received"}),
    (ItemErrorIn, {"error_type": "off_center"}),
    (ItemErrorsRequest, {"errors": []}),
    (FriedbergNumberCreate, {"fr_number": "9960"}),
    (FriedbergAttachIn, {"friedberg_id": 1, "status": "proposed"}),
]


@pytest.mark.parametrize(
    ("schema", "body"), _ACCEPTED, ids=[schema.__name__ for schema, _ in _ACCEPTED]
)
def test_a_key_the_route_does_not_read_is_refused(
    schema: type[BaseModel], body: dict[str, Any]
) -> None:
    """A misspelt field must not answer 200 for a change never made."""
    schema.model_validate(body)

    with pytest.raises(ValidationError, match="misspelt"):
        schema.model_validate({**body, "misspelt": 1})


def test_a_key_inside_a_split_piece_or_an_error_row_is_refused_too() -> None:
    with pytest.raises(ValidationError, match="misspelt"):
        SplitRequest.model_validate(
            {"pieces": [{"source_title": "A", "misspelt": 1}, {"source_title": "B"}]}
        )
    with pytest.raises(ValidationError, match="misspelt"):
        ItemErrorsRequest.model_validate(
            {"errors": [{"error_type": "off_center", "misspelt": 1}]}
        )


# ---------------------------------------------------------------------------
# Numbers held to the columns they are stored in
# ---------------------------------------------------------------------------


def test_a_fineness_of_nought_is_refused_on_an_edit_as_on_a_new_item() -> None:
    """`ck_inventory_item_fineness_fraction` requires more than zero."""
    with pytest.raises(ValidationError, match="fineness"):
        InventoryItemUpdate.model_validate({"fineness": "0"})
    with pytest.raises(ValidationError, match="fineness"):
        ItemCreate.model_validate(
            {
                "purchase_order_id": 1,
                "item_kind": "bullion",
                "source_title": "A bar",
                "fineness": "0",
            }
        )
    assert InventoryItemUpdate.model_validate({"fineness": "0.9000"}).fineness


@pytest.mark.parametrize("field", ["gross_weight_ozt", "fine_weight_ozt"])
@pytest.mark.parametrize("value", ["10000000", "1.0000001", "-1"])
def test_a_weight_its_column_cannot_hold_is_refused(field: str, value: str) -> None:
    """NUMERIC(12,6): six whole digits, six places, never negative."""
    with pytest.raises(ValidationError, match=field):
        InventoryItemUpdate.model_validate({field: value})
    with pytest.raises(ValidationError, match=field):
        ItemCreate.model_validate(
            {
                "purchase_order_id": 1,
                "item_kind": "bullion",
                "source_title": "A bar",
                field: value,
            }
        )


def test_a_weight_its_column_can_hold_is_taken() -> None:
    edit = InventoryItemUpdate.model_validate(
        {"gross_weight_ozt": "999999.999999", "fine_weight_ozt": "0.072340"}
    )
    assert str(edit.gross_weight_ozt) == "999999.999999"
    assert str(edit.fine_weight_ozt) == "0.072340"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("commission_rate", "0.13255"),
        ("processing_rate", "0.02905"),
        ("commission_rate", "1.5"),
        ("processing_fixed", "0.305"),
        ("listing_fee", "0.005"),
        ("listing_fee", "100000000000"),
        ("processing_fixed", "-0.30"),
    ],
)
def test_a_platform_fee_its_column_would_round_or_refuse_is_refused(
    field: str, value: str
) -> None:
    """Rates are NUMERIC(6,4) fractions; fixed fees are NUMERIC(12,2).

    Each body is otherwise valid -- the update carries its `version` -- so
    the one error raised is the field's own. A body missing a required key
    fails on that key, and its message quotes the whole body, field name
    included, so `match` would pass whatever the field's bounds were.
    """
    with pytest.raises(ValidationError, match=field):
        SalesVenueUpdate.model_validate({field: value, "version": 1})
    with pytest.raises(ValidationError, match=field):
        SalesVenueCreate.model_validate(
            {"code": "test", "name": "Test", "kind": "marketplace", field: value}
        )


def test_a_platform_fee_its_column_holds_is_taken() -> None:
    venue = SalesVenueUpdate.model_validate(
        {
            "commission_rate": "0.1325",
            "processing_rate": "1",
            "processing_fixed": "0.30",
            "listing_fee": "0",
            "version": 1,
        }
    )
    assert str(venue.commission_rate) == "0.1325"
    assert str(venue.processing_fixed) == "0.30"


#: The largest value an INTEGER column holds.
_INT4_MAX = 2**31 - 1

#: A body each schema accepts, the integer field it stores in an INTEGER
#: column, and nothing else wrong with it.
_INTEGER_FIELDS: list[tuple[type[BaseModel], dict[str, Any], str]] = [
    (
        ItemCreate,
        {"purchase_order_id": 1, "item_kind": "bullion", "source_title": "A bar"},
        "piece_count",
    ),
    (InventoryItemUpdate, {}, "piece_count"),
    (SplitPieceIn, {"source_title": "A piece"}, "piece_count"),
    (
        OfferIn,
        {"venue": "ebay", "items": [{"item_id": 1, "price": "1.00"}]},
        "quantity",
    ),
    (ImageLinkIn, {"inventory_item_id": 1}, "sort_order"),
]


@pytest.mark.parametrize(
    ("schema", "body", "field"),
    _INTEGER_FIELDS,
    ids=[f"{schema.__name__}.{field}" for schema, _, field in _INTEGER_FIELDS],
)
def test_an_integer_its_column_cannot_hold_is_refused(
    schema: type[BaseModel], body: dict[str, Any], field: str
) -> None:
    """Past the INTEGER range is a 422 here, not a `DataError` (a 500) on write."""
    schema.model_validate({**body, field: _INT4_MAX})
    with pytest.raises(ValidationError, match=field):
        schema.model_validate({**body, field: _INT4_MAX + 1})


#: A body each schema accepts, a text field it stores in a VARCHAR column,
#: and that column's length.
_TEXT_FIELDS: list[tuple[type[BaseModel], dict[str, Any], str, int]] = [
    (UserUpdate, {}, "full_name", 255),
    (CustomerUpdate, {}, "display_name", 255),
    (AddressIn, {"line1": "1 Main St", "city": "Springfield"}, "line2", 255),
    (AddressIn, {"line1": "1 Main St", "city": "Springfield"}, "region", 128),
    (AddressIn, {"line1": "1 Main St", "city": "Springfield"}, "postal_code", 32),
]


@pytest.mark.parametrize(
    ("schema", "body", "field", "length"),
    _TEXT_FIELDS,
    ids=[f"{schema.__name__}.{field}" for schema, _, field, _ in _TEXT_FIELDS],
)
def test_text_longer_than_its_column_is_refused(
    schema: type[BaseModel], body: dict[str, Any], field: str, length: int
) -> None:
    """Past the column's length is a 422 here, not a `DataError` (a 500) on write."""
    schema.model_validate({**body, field: "x" * length})
    with pytest.raises(ValidationError, match=field):
        schema.model_validate({**body, field: "x" * (length + 1)})


# ---------------------------------------------------------------------------
# Text read the same way wherever it is entered
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", ["a/b", "/", "MS/65"])
def test_a_vocabulary_code_holds_no_slash(code: str) -> None:
    """A code is one segment of `/api/reference/{table}/{code}`."""
    with pytest.raises(ValidationError, match="code"):
        ReferenceValueCreate.model_validate({"code": code, "label": "Test"})


@pytest.mark.parametrize("code", ["MS64PL", "usd_coin_0_07", "64+", "5FS", "a.b-c"])
def test_a_vocabulary_code_keeps_the_marks_the_shipped_ones_use(code: str) -> None:
    made = ReferenceValueCreate.model_validate({"code": code, "label": "Test"})
    assert made.code == code


@pytest.mark.parametrize("url", ["ftp://example.com", "javascript:alert(1)", "shop"])
def test_a_vendors_link_is_a_web_address_on_a_correction_as_when_added(
    url: str,
) -> None:
    with pytest.raises(ValidationError, match="http"):
        VendorCreate.model_validate({"name": "A vendor", "url": url})
    with pytest.raises(ValidationError, match="http"):
        VendorUpdate.model_validate({"url": url})


@pytest.mark.parametrize(
    "template",
    [
        "javascript:alert('{external_id}')",
        "JavaScript:alert('{external_id}')",
        "data:text/html,{external_id}",
        "//example.com/itm/{external_id}",
    ],
)
def test_a_platform_s_listing_link_template_is_a_web_address(template: str) -> None:
    """It becomes a listing's link, so it is held to http(s) like every other."""
    with pytest.raises(ValidationError, match="http"):
        SalesVenueCreate.model_validate(
            {
                "code": "test",
                "name": "Test",
                "kind": "marketplace",
                "listing_url_template": template,
            }
        )
    with pytest.raises(ValidationError, match="http"):
        SalesVenueUpdate.model_validate(
            {"listing_url_template": template, "version": 1}
        )


def test_a_platform_s_listing_link_template_on_the_web_is_taken() -> None:
    for template in (
        "https://www.example.com/itm/{external_id}",
        "HTTP://example.com/lot/{external_id}",
    ):
        made = SalesVenueUpdate.model_validate(
            {"listing_url_template": template, "version": 1}
        )
        assert made.listing_url_template == template
    with pytest.raises(ValidationError, match="external_id"):
        SalesVenueUpdate.model_validate(
            {"listing_url_template": "https://example.com/itm", "version": 1}
        )


_PURCHASE = {
    "id": 1,
    "order_number": "A-1",
    "vendor": "A vendor",
    "ordered_on": None,
    "lines": [],
}


@pytest.mark.parametrize(
    ("sent", "offered"),
    [
        ("javascript:alert(1)", None),
        ("data:text/html,x", None),
        ("Gift", None),
        ("https://example.com/store", "https://example.com/store"),
        ("MAILTO:coins@example.com", "MAILTO:coins@example.com"),
    ],
)
def test_a_seller_s_address_is_offered_only_as_a_link_that_is_safe(
    sent: str, offered: str | None
) -> None:
    """A row written around the entry rule must not reach an `href`."""
    purchase = PurchaseOrderDetailOut.model_validate({**_PURCHASE, "seller_url": sent})
    assert purchase.seller_url == offered


@pytest.mark.parametrize("field", ["source_url", "lot_page"])
def test_a_purchase_s_page_is_offered_only_on_the_web(field: str) -> None:
    for sent in ("javascript:alert(1)", "mailto:coins@example.com"):
        purchase = PurchaseOrderDetailOut.model_validate({**_PURCHASE, field: sent})
        assert getattr(purchase, field) is None, sent
    kept = PurchaseOrderDetailOut.model_validate(
        {**_PURCHASE, field: "https://example.com/lot/1"}
    )
    assert getattr(kept, field) == "https://example.com/lot/1"


def test_a_photograph_s_source_is_offered_only_on_the_web() -> None:
    body = {
        "id": 1,
        "sha256": "0" * 64,
        "media_type": "image/jpeg",
        "byte_size": 1,
        "thumbnail_url": "/t",
        "image_url": "/i",
    }
    assert (
        ImageOut.model_validate({**body, "source_url": "javascript:x"}).source_url
        is None
    )
    assert (
        ImageOut.model_validate(
            {**body, "source_url": "https://example.com/a.jpg"}
        ).source_url
        == "https://example.com/a.jpg"
    )
    link = ImageLinkOut.model_validate(
        {
            "image_id": 1,
            "thumbnail_url": "/t",
            "image_url": "/i",
            "source_url": "data:x",
        }
    )
    assert link.source_url is None


def test_a_vendors_link_is_trimmed_and_a_blank_one_clears_it() -> None:
    assert VendorUpdate.model_validate({"url": "   "}).url is None
    assert (
        VendorUpdate.model_validate({"url": "  https://example.com/x "}).url
        == "https://example.com/x"
    )


def test_a_new_purchases_link_is_read_as_a_corrected_one_is() -> None:
    """Trimmed, blank is none, otherwise http(s)."""
    blank = PurchaseOrderCreate.model_validate({"vendor_id": 1, "source_url": "  "})
    assert blank.source_url is None
    padded = PurchaseOrderCreate.model_validate(
        {"vendor_id": 1, "source_url": "  https://example.com/order/1 "}
    )
    assert padded.source_url == "https://example.com/order/1"
    with pytest.raises(ValidationError, match="http"):
        PurchaseOrderCreate.model_validate({"vendor_id": 1, "source_url": "Gift"})


@pytest.mark.parametrize("name", ["", "   ", "\t"])
def test_a_customer_cannot_be_renamed_to_nothing(name: str) -> None:
    with pytest.raises(ValidationError, match="blank"):
        CustomerUpdate.model_validate({"display_name": name})


def test_a_customers_name_is_trimmed_and_an_omitted_one_is_left_alone() -> None:
    renamed = CustomerUpdate.model_validate({"display_name": " Pat "})
    assert renamed.display_name == "Pat"
    assert "display_name" not in CustomerUpdate.model_validate({}).model_fields_set


def test_a_district_letter_is_one_of_twelve_in_capitals() -> None:
    made = FriedbergNumberCreate.model_validate(
        {"fr_number": "9961-B", "district_letter": "b"}
    )
    assert made.district_letter == "B"
    for letter in ("M", "z", "1", "*"):
        with pytest.raises(ValidationError, match="A to L"):
            FriedbergNumberCreate.model_validate(
                {"fr_number": "9962", "district_letter": letter}
            )
