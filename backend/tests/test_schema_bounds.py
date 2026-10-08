"""What a request body may hold, checked where it is declared (`app.schemas`).

A value the database would refuse or round, and a key the route does not
read, are refused at validation: the first would otherwise surface as a
server error or be stored as something else, the second would answer 200
for a change never made. Nothing here opens a connection.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.schemas import (
    CustomerUpdate,
    FriedbergAttachIn,
    FriedbergNumberCreate,
    ImageLinkIn,
    ImageLinkUpdate,
    InventoryItemUpdate,
    ItemCreate,
    ItemErrorIn,
    ItemErrorsRequest,
    OrderStatusUpdate,
    PurchaseOrderCreate,
    ReceiveRequest,
    ReferenceAliasIn,
    ReferenceMergeIn,
    ReferenceValueCreate,
    ReferenceValueRename,
    SalesVenueCreate,
    SalesVenueUpdate,
    SplitPieceIn,
    SplitRequest,
    VendorCreate,
    VendorUpdate,
)
from pydantic import BaseModel, ValidationError

#: A body each schema accepts, to which one unknown key is then added.
_ACCEPTED: list[tuple[type[BaseModel], dict[str, Any]]] = [
    (OrderStatusUpdate, {"status": "paid"}),
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
    """Rates are NUMERIC(6,4) fractions; fixed fees are NUMERIC(12,2)."""
    with pytest.raises(ValidationError, match=field):
        SalesVenueUpdate.model_validate({field: value})
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
        }
    )
    assert str(venue.commission_rate) == "0.1325"
    assert str(venue.processing_fixed) == "0.30"


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
