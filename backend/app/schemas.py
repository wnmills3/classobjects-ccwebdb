"""Pydantic request/response models.

**Classifiers cross the API as codes, not ids.** A client sends
``"item_kind": "bullion"``, never ``"item_kind_id": 3``. Ids are internal and
may differ between installations; codes are the stable contract. The routers
resolve them, and an unknown code is a 422 rather than a silently null column.

**Money and weight are `Decimal`.** They are declared with explicit precision
so a client sending a float gets a validation error rather than a rounding
surprise several layers down.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from pydantic_core.core_schema import ValidationInfo

from .models import UserRole

# --------------------------------------------------------------------------
# Auth / users
# --------------------------------------------------------------------------


class UserCreate(BaseModel):
    """Registration payload. The password is never stored or echoed back."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(default="", max_length=255)


class UserOut(BaseModel):
    """A user as the API returns them -- no password material of any kind."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    full_name: str
    role: UserRole
    is_active: bool
    created_at: datetime


class TokenPair(BaseModel):
    """The two tokens issued on login: a short access one and a long refresh one."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    """Exchange a refresh token for a new access token."""

    refresh_token: str


# --------------------------------------------------------------------------
# Catalogue
#
# A catalogue entry is a `listing` joined to the `inventory_item` behind it.
# The two are separate tables because an item may be listed, delisted and
# relisted at different prices, and because the public catalogue must be able
# to show a listing without exposing the item's cost basis or location. The
# API presents them as one resource, since that is how a shop is operated.
# --------------------------------------------------------------------------


class CatalogItemBase(BaseModel):
    """Fields shared by creating and reading a catalogue entry."""

    title: str = Field(min_length=1, max_length=500)
    description: str = ""

    # Classifiers, by code.
    item_kind: str = Field(default="coin", max_length=64)
    country: str | None = Field(default=None, max_length=64)
    denomination: str | None = Field(default=None, max_length=64)
    bullion_form: str | None = Field(default=None, max_length=64)
    grade: str | None = Field(default=None, max_length=64)
    grading_service: str | None = Field(default=None, max_length=64)
    metal: str | None = Field(default=None, max_length=64)

    year_start: int | None = Field(default=None, ge=-3000, le=2200)
    year_end: int | None = Field(default=None, ge=-3000, le=2200)

    fineness: Decimal | None = Field(default=None, ge=0, le=1, decimal_places=4)
    gross_weight_ozt: Decimal | None = Field(default=None, ge=0, decimal_places=6)
    fine_weight_ozt: Decimal | None = Field(default=None, ge=0, decimal_places=6)

    #: Pieces in the lot itself -- a roll of 50 is one item with quantity 50.
    #: Distinct from `quantity_available`, which is how many are for sale.
    storage_quantity: int = Field(default=1, ge=1)

    price: Decimal = Field(ge=Decimal("0"), max_digits=12, decimal_places=2)
    currency: str = Field(default="USD", max_length=8)
    quantity_available: int = Field(default=1, ge=0)
    is_active: bool = True

    @field_validator("year_end")
    @classmethod
    def year_range_must_not_be_backwards(
        cls, value: int | None, info: ValidationInfo
    ) -> int | None:
        """Reject a range that ends before it starts.

        Caught here rather than only by the database check constraint, so the
        caller gets a field-level message instead of a 500.
        """
        start = info.data.get("year_start")
        if value is not None and start is not None and value < start:
            raise ValueError("year_end must not be earlier than year_start")
        return value


class CatalogItemCreate(CatalogItemBase):
    """Creates an inventory item and the listing that offers it, together."""


class CatalogItemUpdate(BaseModel):
    """All fields optional -- only what is supplied gets changed.

    ``version`` is the token the client last read, returned by any GET.
    Supplying it makes the update conditional: if someone else has saved in
    the meantime the request is refused with 409, rather than silently
    overwriting their work with values loaded before their change.

    It is an opaque string, not a number, because a catalogue entry is two
    rows -- the listing and the inventory item behind it -- each versioned
    separately. Checking only one of them misses edits to the other, which is
    exactly the bug the first implementation had: renaming an item changed the
    *item* row, so a stale form whose listing version still matched was
    accepted and overwrote the new title.

    Optional, so a script that genuinely means "set this regardless" can omit
    it -- but the edit form always sends it.
    """

    version: str | None = None

    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = None
    item_kind: str | None = Field(default=None, max_length=64)
    country: str | None = Field(default=None, max_length=64)
    denomination: str | None = Field(default=None, max_length=64)
    bullion_form: str | None = Field(default=None, max_length=64)
    grade: str | None = Field(default=None, max_length=64)
    grading_service: str | None = Field(default=None, max_length=64)
    metal: str | None = Field(default=None, max_length=64)
    year_start: int | None = Field(default=None, ge=-3000, le=2200)
    year_end: int | None = Field(default=None, ge=-3000, le=2200)
    fineness: Decimal | None = Field(default=None, ge=0, le=1, decimal_places=4)
    gross_weight_ozt: Decimal | None = Field(default=None, ge=0, decimal_places=6)
    fine_weight_ozt: Decimal | None = Field(default=None, ge=0, decimal_places=6)
    storage_quantity: int | None = Field(default=None, ge=1)
    price: Decimal | None = Field(
        default=None, ge=Decimal("0"), max_digits=12, decimal_places=2
    )
    quantity_available: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


class ImageOut(BaseModel):
    """A stored photograph and the URLs its renditions are served from.

    No URL for the original: public requests are answered only from
    derivatives, which is what keeps a file that somehow retained metadata
    unreachable.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    sha256: str
    media_type: str
    byte_size: int
    width: int | None = None
    height: int | None = None
    captured_at: datetime | None = None
    thumbnail_url: str
    image_url: str


class CatalogItemOut(BaseModel):
    """What a buyer sees.

    Deliberately carries no cost basis, storage location or internal catalogue
    number -- see `public_catalog` in the database design. The admin views read
    the same shape, so a field cannot be added here for staff and leak to
    customers.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    inventory_item_id: int
    #: Send this back on a PATCH to save safely. Opaque -- it covers both the
    #: listing and the item behind it. See CatalogItemUpdate.
    version: str
    #: The item's permanent code -- stable across sale, return and relisting,
    #: which is what makes it usable on a packing slip and in an audit.
    item_code: str
    title: str
    description: str

    item_kind: str | None = None
    country: str | None = None
    denomination: str | None = None
    bullion_form: str | None = None
    grade: str | None = None
    grading_service: str | None = None
    metal: str | None = None

    year_start: int | None = None
    year_end: int | None = None
    fineness: Decimal | None = None
    gross_weight_ozt: Decimal | None = None
    fine_weight_ozt: Decimal | None = None
    storage_quantity: int = 1

    price: Decimal
    currency: str
    quantity_available: int
    is_active: bool

    #: Renditions of the item's primary photograph. Null when it has none --
    #: most of a real collection is unphotographed, and the UI has to cope.
    thumbnail_url: str | None = None
    image_url: str | None = None

    created_at: datetime
    updated_at: datetime


class CatalogPage(BaseModel):
    """A page of catalogue results plus the total matching count."""

    items: list[CatalogItemOut]
    total: int
    limit: int
    offset: int


# --------------------------------------------------------------------------
# Orders
# --------------------------------------------------------------------------


class OrderLineIn(BaseModel):
    """One line of an order: which listing, and how many."""

    listing_id: int
    quantity: int = Field(ge=1)


class OrderCreate(BaseModel):
    """An order as placed. At least one line, and no listing twice."""

    items: list[OrderLineIn] = Field(min_length=1)

    @field_validator("items")
    @classmethod
    def no_duplicate_listings(cls, items: list[OrderLineIn]) -> list[OrderLineIn]:
        """Refuse the same listing twice in one order.

        Two lines for one listing would each be checked against stock
        separately, so together they could pass while overselling it.
        """
        seen = {item.listing_id for item in items}
        if len(seen) != len(items):
            raise ValueError("each listing_id may appear at most once per order")
        return items


class OrderItemOut(BaseModel):
    """An order line as stored, with the price frozen at purchase time."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    listing_id: int
    quantity: int
    unit_price: Decimal


class OrderOut(BaseModel):
    """An order and its lines."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    status: str
    total_amount: Decimal
    placed_at: datetime
    items: list[OrderItemOut]


class OrderStatusUpdate(BaseModel):
    """Advance an order to another status, by `sales_order_status` code."""

    #: A `sales_order_status` code: pending, paid, packed, shipped,
    #: delivered, cancelled, refunded.
    status: str = Field(min_length=1, max_length=64)


# --------------------------------------------------------------------------
# Reference vocabularies
# --------------------------------------------------------------------------


class ReferenceValueOut(BaseModel):
    """One classifier, shaped for a dropdown.

    `code` is what gets submitted, `label` is what a person reads. Table
    specific columns arrive in `extra` rather than being flattened, so a client
    can show a denomination's face value or an error type's `applies_to`
    without the API needing a response model per table.
    """

    code: str
    label: str
    sort_order: int
    source: str
    extra: dict[str, object] = Field(default_factory=dict)


class ReferenceTableOut(BaseModel):
    """One vocabulary, ordered as a picker should show it."""

    table: str
    values: list[ReferenceValueOut]


# --------------------------------------------------------------------------
# Splitting a lot into its pieces
# --------------------------------------------------------------------------


class SplitPieceIn(BaseModel):
    """One piece to create when breaking a lot apart."""

    title: str = Field(min_length=1, max_length=500)
    storage_quantity: int = Field(default=1, ge=1)
    #: The value of ONE piece, on whatever basis the caller chose -- face
    #: value, melt, a catalogue price. Required in `relative` mode, ignored in
    #: `equal`. What it measures is the caller's decision; this only divides
    #: the cost in proportion to it.
    relative_value: Decimal | None = Field(default=None, ge=0)
    #: Classifier overrides for this piece, by code. A mint set's pieces have
    #: different denominations from each other and from the set.
    denomination: str | None = Field(default=None, max_length=64)
    grade: str | None = Field(default=None, max_length=64)
    metal: str | None = Field(default=None, max_length=64)
    year_start: int | None = Field(default=None, ge=-3000, le=2200)


class SplitRequest(BaseModel):
    """How to break a lot into pieces, and how to divide its cost."""

    #: `equal` divides the cost evenly per piece; `relative` divides it in
    #: proportion to each piece's `relative_value`.
    mode: str = Field(default="equal", pattern="^(equal|relative)$")
    pieces: list[SplitPieceIn] = Field(min_length=2)


class SplitResultOut(BaseModel):
    """What the split produced, with the arithmetic laid out to be checked."""

    parent_item_code: str
    mode: str
    #: What the lot cost, and what the pieces cost. `price` and `shipping`
    #: always reconcile exactly.
    parent_price: Decimal
    allocated_price: Decimal
    parent_shipping: Decimal
    allocated_shipping: Decimal
    #: Taxes are generated per row at a fixed rate, so the sum of the pieces'
    #: rounded taxes can differ from the lot's by a cent or two. Reported
    #: rather than hidden -- see the note in the split endpoint.
    parent_total_cost: Decimal
    allocated_total_cost: Decimal
    total_cost_difference: Decimal
    pieces: list[InventoryItemOut]


class InventoryItemOut(BaseModel):
    """An inventory item as staff see it: cost basis included."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    item_code: str
    title: str
    storage_quantity: int
    price: Decimal
    shipping: Decimal
    taxes: Decimal
    total_cost: Decimal
    parent_item_id: int | None = None
    split_at: datetime | None = None


# --------------------------------------------------------------------------
# Inventory browse
# --------------------------------------------------------------------------


class FacetValueOut(BaseModel):
    """One value a filter could take, and how many rows would match it."""

    value: object
    count: int


class InventoryPageOut(BaseModel):
    """A page of inventory rows.

    `rows` are plain dictionaries whose keys are the view's own columns rather
    than a declared model per view. The two views differ in exactly the columns
    that make them worth separating, and restating forty-odd fields twice in
    Python would add no safety over the view definition while adding a second
    place to drift. The returned column set is fixed by the view specification,
    not by `select *`.

    Money and weight arrive as strings, never floats.
    """

    view: str
    rows: list[dict[str, object]]
    total: int
    limit: int
    offset: int
    sort: str
    descending: bool
    facets: dict[str, list[FacetValueOut]] = Field(default_factory=dict)


class ReferenceValueCreate(BaseModel):
    """Add a value to a vocabulary while picking from it.

    This is how the tables grow organically: an operator entering an item that
    does not fit the shipped vocabulary adds the missing value in place rather
    than abandoning the entry or forcing it into an approximate one.

    Created rows are marked `manual`, so they stay distinguishable from the
    shipped catalogue and are excluded from an export by default.
    """

    code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_+./-]+$")
    label: str = Field(min_length=1, max_length=255)
    sort_order: int = 500
    #: Columns specific to the table, such as a denomination's face value.
    extra: dict[str, object] = Field(default_factory=dict)


class ReferenceValueRename(BaseModel):
    """Change what a value is *called*.

    Only the label. The code is the API contract -- it appears in saved
    filters, bookmarked searches and any integration -- so it is immutable,
    and renaming is exactly the operation that lets a badly-worded label be
    fixed without breaking those.

    Because every record refers to the value by foreign key, a rename takes
    effect everywhere at once. There is nothing to migrate.
    """

    label: str = Field(min_length=1, max_length=255)
    sort_order: int | None = None
    is_active: bool | None = None
