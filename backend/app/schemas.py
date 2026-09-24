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

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

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


class ImageLinkOut(BaseModel):
    """One photograph as it is filed: the link, and the image it points at.

    Both together because every console surface that lists photographs needs
    both, and a second request per thumbnail is a page that arrives in pieces.
    """

    model_config = ConfigDict(from_attributes=True)

    #: Null for a photograph nobody has filed yet.
    link_id: int | None = None
    inventory_item_id: int | None = None
    item_code: str | None = None
    image_id: int
    image_role: str | None = None
    is_primary: bool = False
    sort_order: int = 0
    captured_at: datetime | None = None
    #: These two names are `routers.images.image_urls`'s own keys, so the
    #: helper can be splatted straight in. It returns `image_url`, not
    #: `web_url` -- matching `ImageOut`, which does the same.
    thumbnail_url: str
    image_url: str


class ImageLinkUpdate(BaseModel):
    """What may change about a filed photograph."""

    image_role: str | None = None
    is_primary: bool | None = None
    #: Set after a refusal (app.sale_state).
    acknowledge_for_sale: bool = False


class ImageLinkIn(BaseModel):
    """Filing a photograph against an item."""

    inventory_item_id: int
    image_role: str | None = None
    is_primary: bool = False
    sort_order: int = 0
    #: Set after a refusal to say the caller knows the item is for sale
    #: (app.sale_state). The shop serves an item's primary photograph.
    acknowledge_for_sale: bool = False


class CatalogMemberOut(BaseModel):
    """One coin inside a lot, as a buyer sees it.

    The descriptive half of `CatalogItemOut` and nothing else: a member has
    no price, no stock and no listing of its own, because the lot is the one
    thing for sale. Every field here is one a single-item catalogue entry
    already shows a customer, so nothing becomes public by being a member --
    and cost basis and storage location are absent for the same reason they
    are absent there.
    """

    model_config = ConfigDict(from_attributes=True)

    inventory_item_id: int
    #: The item's permanent code, as `CatalogItemOut.item_code` is for a coin
    #: sold on its own.
    item_code: str
    title: str
    description: str

    item_kind: str | None = None
    country: str | None = None
    denomination: str | None = None
    bullion_form: str | None = None
    grade: str | None = None
    strike_type: str | None = None
    #: As collectors write it: MS65, PR69+. `grade` is the code alone.
    grade_display: str | None = None
    grading_service: str | None = None
    metal: str | None = None

    year_start: int | None = None
    year_end: int | None = None
    fineness: Decimal | None = None
    gross_weight_ozt: Decimal | None = None
    fine_weight_ozt: Decimal | None = None
    piece_count: int = 1

    #: Renditions of this member's primary photograph, null when it has none
    #: -- the same shape, and the same caveat, as `CatalogItemOut`'s.
    thumbnail_url: str | None = None
    image_url: str | None = None


class CatalogItemOut(BaseModel):
    """What a buyer sees: one coin, or one lot of them.

    Deliberately carries no cost basis, storage location or internal catalogue
    number -- see `public_catalog` in the database design. The admin views read
    the same shape, so a field cannot be added here for staff and leak to
    customers.

    **A listing offers an item or a lot, never both** (`ck_listing_item_xor_lot`),
    which is why the two item-only fields below are optional: a lot listing's
    `inventory_item_id` is NULL and it has no item code of its own. Its coins
    are in `members`, and the item-describing fields -- kind, grade, metal,
    year -- stay null, because no single value of any of them describes a
    group.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    #: Null on a lot listing; `members` carries the coins instead.
    inventory_item_id: int | None = None
    #: Opaque -- it covers both the listing and the item behind it, so a
    #: caller cannot use it to detect a change to only one of them.
    version: str
    #: The item's permanent code -- stable across sale, return and relisting,
    #: which is what makes it usable on a packing slip and in an audit. Null
    #: on a lot listing: a lot is not an item and has no code.
    item_code: str | None = None
    title: str
    description: str

    item_kind: str | None = None
    country: str | None = None
    denomination: str | None = None
    bullion_form: str | None = None
    grade: str | None = None
    strike_type: str | None = None
    #: As collectors write it: MS65, PR69+. `grade` is the code alone.
    grade_display: str | None = None
    grading_service: str | None = None
    metal: str | None = None

    year_start: int | None = None
    year_end: int | None = None
    fineness: Decimal | None = None
    gross_weight_ozt: Decimal | None = None
    fine_weight_ozt: Decimal | None = None
    #: How many objects this entry is: the item's own count for a coin, and
    #: the **sum** of its members' for a lot. Summed rather than counted
    #: because a member may itself be a multi-piece row, and never left at 1
    #: for a group -- a wrong count reads exactly like a genuine single piece.
    piece_count: int = 1

    price: Decimal
    currency: str
    quantity_available: int
    is_active: bool

    #: Renditions of the item's primary photograph. Null when it has none --
    #: most of a real collection is unphotographed, and the UI has to cope.
    #: Null on a lot listing too: a lot has no photograph of its own, and its
    #: members carry theirs.
    thumbnail_url: str | None = None
    image_url: str | None = None

    #: The coins the lot is made of, in item id order -- the one order every
    #: lot reader uses. Empty for a listing that offers a single item, so a
    #: client can read this field without first asking which kind of entry it
    #: holds, and never empty for a lot.
    #:
    #: Past tense once the listing has ended: a lot releases every membership
    #: when it is sold or dissolved, so these are the coins the group **held**
    #: -- after a dissolved lot, one of them may already be on sale again on
    #: its own. `lot_writes.members_held` is what answers that question;
    #: `offered_items`, which answers "offered now", would say none.
    members: list[CatalogMemberOut] = Field(default_factory=list)

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


def _refuse_duplicate_listings(listing_ids: list[int]) -> None:
    """Refuse the same listing twice in one order.

    Two lines for one listing would each be checked against stock separately,
    so together they could pass while overselling it.
    """
    if len(set(listing_ids)) != len(listing_ids):
        raise ValueError("each listing_id may appear at most once per order")


class OrderCreate(BaseModel):
    """An order as placed. At least one line, and no listing twice."""

    items: list[OrderLineIn] = Field(min_length=1)

    @field_validator("items")
    @classmethod
    def no_duplicate_listings(cls, items: list[OrderLineIn]) -> list[OrderLineIn]:
        """Refuse the same listing twice in one order."""
        _refuse_duplicate_listings([item.listing_id for item in items])
        return items


class AdminOrderLineIn(BaseModel):
    """A line an administrator enters. No price means the listing's price."""

    model_config = ConfigDict(extra="forbid")

    listing_id: int
    quantity: int = Field(ge=1)
    unit_price: Decimal | None = Field(
        default=None, ge=Decimal("0"), max_digits=12, decimal_places=2
    )


class AdminOrderCreate(BaseModel):
    """An order an administrator places for a customer."""

    model_config = ConfigDict(extra="forbid")

    items: list[AdminOrderLineIn] = Field(min_length=1)
    notes: str | None = None

    @field_validator("items")
    @classmethod
    def no_duplicate_listings(
        cls, items: list[AdminOrderLineIn]
    ) -> list[AdminOrderLineIn]:
        """Refuse the same listing twice in one order."""
        _refuse_duplicate_listings([item.listing_id for item in items])
        return items


class OrderItemOut(BaseModel):
    """An order line as stored, with the price frozen at purchase time."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    listing_id: int
    #: What was bought, as it was called when it was sold.
    title: str
    quantity: int
    unit_price: Decimal
    #: The item and listing as sold (app.sale_snapshot). Console only: it
    #: holds costs. None for a shopper, and for lines older than snapshots.
    snapshot: dict[str, object] | None = None
    #: Whether the listing this line bought from has ended -- a lot bought in
    #: the shop, or any outside sale. Cancelling an unshipped order with such
    #: a line is refused (`routers.orders._no_stock_to_return`); the console
    #: reads this to grey the option out instead of offering it and failing.
    listing_ended: bool = False


class OrderOut(BaseModel):
    """An order and its lines."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    #: Who placed it. A customer only ever sees their own orders, so this adds
    #: nothing they did not know; the owner console needs it to say who.
    customer_name: str
    customer_email: str | None
    #: The platform this order sold on. Safe for a shopper: which platform a
    #: sale happened on is not staff-only, and a store order simply reads
    #: `store`.
    sales_venue_code: str
    sales_venue_name: str
    status: str
    total_amount: Decimal
    placed_at: datetime
    items: list[OrderItemOut]
    version: int
    notes: str | None
    #: The account that entered the order, when known.
    placed_by_email: str | None
    #: A paid order whose total changed after it was paid. Payments are not
    #: recorded, so this prompts a person; it never charges or refunds.
    payment_adjustment_due: bool


class OrderRevisionLineIn(BaseModel):
    """A line of an order's desired contents, at the price it should carry."""

    model_config = ConfigDict(extra="forbid")

    listing_id: int
    quantity: int = Field(ge=1)
    unit_price: Decimal = Field(ge=Decimal("0"), max_digits=12, decimal_places=2)


class OrderRevision(BaseModel):
    """An order's complete desired contents, and the version they were read at."""

    model_config = ConfigDict(extra="forbid")

    version: int
    customer_id: int
    items: list[OrderRevisionLineIn] = Field(min_length=1)
    notes: str | None = None

    @field_validator("items")
    @classmethod
    def no_duplicate_listings(
        cls, items: list[OrderRevisionLineIn]
    ) -> list[OrderRevisionLineIn]:
        """Refuse the same listing twice in one order."""
        _refuse_duplicate_listings([item.listing_id for item in items])
        return items


class OrderStatusUpdate(BaseModel):
    """Advance an order to another status, by `sales_order_status` code."""

    #: A `sales_order_status` code: pending, paid, packed, shipped,
    #: delivered, cancelled, refunded.
    status: str = Field(min_length=1, max_length=64)


class OrderChangeOut(BaseModel):
    """One row of an order's history, with who made it and which line."""

    id: int
    changed_at: datetime
    changed_by_email: str | None
    change: str
    listing_id: int | None
    listing_title: str | None
    from_value: str | None
    to_value: str | None


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
    #: False for a retired value, which only include_inactive lists.
    is_active: bool = True
    #: False for a value the application looks up by its code, which may be
    #: renamed but not retired.
    retirable: bool = True
    extra: dict[str, object] = Field(default_factory=dict)
    #: Other names people use for it ("Mercury", "Legal Tender"), which
    #: search, import and the pickers also recognise.
    aliases: list[str] = Field(default_factory=list)
    #: Shipped aliases someone removed, listed only with include_inactive so
    #: the console can offer them back.
    retired_aliases: list[str] = Field(default_factory=list)


class ReferenceTableOut(BaseModel):
    """One vocabulary, ordered as a picker should show it."""

    table: str
    values: list[ReferenceValueOut]
    #: True when the values are in a meaningful order (`sort_order`) rather
    #: than alphabetical: grades, denominations, lifecycles. The Vocabularies
    #: page offers to change the order only for these.
    sequenced: bool = False


# --------------------------------------------------------------------------
# Splitting a lot into its pieces
# --------------------------------------------------------------------------


class SplitPieceIn(BaseModel):
    """One piece to create when breaking a lot apart."""

    source_title: str = Field(min_length=1, max_length=500)
    piece_count: int = Field(default=1, ge=1)
    #: The value of ONE piece, on whatever basis the caller chose -- face
    #: value, melt, a catalogue price. Required in `relative` mode, ignored in
    #: `equal`. What it measures is the caller's decision; this only divides
    #: the cost in proportion to it.
    relative_value: Decimal | None = Field(default=None, ge=0)
    #: Classifier overrides for this piece, by code. A mint set's pieces have
    #: different denominations from each other and from the set.
    denomination: str | None = Field(default=None, max_length=64)
    #: A number (65, 64+) or a compound grade (MS65, PR69+), which is split
    #: into the number and `strike_type`.
    grade: str | None = Field(default=None, max_length=64)
    strike_type: str | None = Field(default=None, max_length=64)
    metal: str | None = Field(default=None, max_length=64)
    year_start: int | None = Field(default=None, ge=-3000, le=2200)


class SplitRequest(BaseModel):
    """How to break a lot into pieces, and how to divide its cost."""

    #: `equal` divides the cost evenly per piece; `relative` divides it in
    #: proportion to each piece's `relative_value`.
    mode: str = Field(default="equal", pattern="^(equal|relative)$")
    pieces: list[SplitPieceIn] = Field(min_length=2)
    #: Set after a refusal to say the caller knows the lot is offered
    #: somewhere (app.sale_state). An order refuses the split regardless.
    acknowledge_for_sale: bool = False


class SplitResultOut(BaseModel):
    """What the split produced, with the arithmetic laid out to be checked."""

    parent_item_code: str
    mode: str
    #: What the lot cost, and what the pieces cost. `item_cost` and
    #: `shipping_cost` always reconcile exactly.
    parent_cost: Decimal
    allocated_cost: Decimal
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
    version: int
    source_title: str
    year_start: int | None = None
    piece_count: int
    item_cost: Decimal
    shipping_cost: Decimal
    sales_tax: Decimal
    total_cost: Decimal
    parent_item_id: int | None = None
    split_at: datetime | None = None


class ItemAttributeOut(BaseModel):
    """One attribute an item carries."""

    code: str
    label: str
    #: serial, variety, release, verification or qualifier.
    group: str
    #: `derived` (read by a rule or the importer) or `manual`.
    source: str
    #: The rule that read it: `serial_pattern`, `import`.
    derived_by: str | None = None


class SaleUseOut(BaseModel):
    """One reason an item is for sale: a listing, or an open order."""

    kind: str
    id: int
    text: str


class ItemSaleOut(BaseModel):
    """One sale of an item: the order line, and the item as it was sold.

    **`quantity` and `unit_price` are the *line*'s, not the coin's.** For a
    lot line they describe the whole group -- one lot at 1,000.00 -- so every
    member of a three-coin lot would otherwise report the whole price as its
    own. `sales_lot_id` says the line was a group sale, and `share_amount`
    is what this coin was actually credited with inside it. A reader showing
    money against one coin wants `share_amount` whenever it is there.
    """

    order_id: int
    status: str
    placed_at: datetime
    customer_name: str
    #: The line's quantity and unit price -- the lot's, for a lot line.
    quantity: int
    unit_price: Decimal
    #: The lot this line sold, or null for an ordinary item sale. The one
    #: field that says "this coin went out inside a group".
    sales_lot_id: int | None = None
    #: This coin's share of the line, cost-weighted by `total_cost`
    #: (`order_writes._sync_shares`). It equals the line total for an item
    #: sale and is a fraction of it for a lot member. Null only for a line
    #: written before shares existed, which `_sync_shares` still allows for.
    #: A `Decimal` on a Pydantic model, so it crosses the wire as a string.
    share_amount: Decimal | None = None
    #: The item and listing when the line was made; None for older lines.
    snapshot: dict[str, object] | None = None
    snapshot_at: datetime | None = None


class FieldChangeOut(BaseModel):
    """A field's most recent change by a person's edit: who, and when."""

    by: str | None
    at: datetime


class ItemHistoryEventOut(BaseModel):
    """One entry in an item's history (`app.item_history`).

    `kind` says which log it came from: `field` (an edit, `field` naming the
    field as the editor does), `status` or `location`. Values are shown as a
    person reads them -- a classifier's label, a location's name -- and may
    be a list, for `attributes`. `old_value` is null on an item's first
    status and first location.
    """

    kind: Literal["field", "status", "location"]
    field: str
    old_value: Any = None
    new_value: Any = None
    by: str | None
    at: datetime
    note: str | None = None
    arrived_on: date | None = None


class ItemDetailOut(InventoryItemOut):
    """One item, with everything the edit form needs in one round trip.

    The two provenance mechanisms answer different questions and both appear
    here. `lot_claims` is *derived* by comparing the item to its parent and
    says what the lot claimed -- it cannot drift out of sync because it is
    recomputed. `reviewed` is *asserted* and says a person looked -- it cannot
    be computed from anything. Neither replaces the other.
    """

    #: The lot this piece came out of, if any. Absent for every item as of
    #: 2026-09-20, nothing having been split yet: no parent is the normal
    #: state, not an orphan.
    parent_item_code: str | None = None
    #: What the lot said, for the fields a piece inherits. The form shows
    #: these beside the item's own values, so it is always visible what is
    #: being overridden and what is still only the seller's word.
    lot_claims: dict[str, object] = Field(default_factory=dict)
    #: Fields a person has confirmed by examination.
    reviewed: list[str] = Field(default_factory=list)
    #: Fields holding a default filled from known facts, as column to the rule
    #: that filled it (`note_type_id`: `note_issue`). The form marks them as
    #: suggestions; saving one by hand makes it the person's.
    derived: dict[str, str] = Field(default_factory=dict)

    # -- the rest of EDITABLE_SCALARS: not on InventoryItemOut, which is the
    # shape a split's pieces come back as and has no reason to carry these.
    description: str = ""
    year_end: int | None = None
    fineness: Decimal | None = None
    gross_weight_ozt: Decimal | None = None
    fine_weight_ozt: Decimal | None = None
    #: What the item is worth to a collector, as last recorded -- the
    #: "value" column the offer dialog shows beside cost when pricing.
    #: Read-only here: nothing in the editor sets it yet.
    numismatic_value: Decimal | None = None
    tax_rate: Decimal
    tax_includes_shipping: bool
    #: The rate a new item would be stamped with today. The form's "No sales
    #: tax charged" box restores it when unticked on an item recorded as
    #: untaxed, which has no non-zero rate of its own to go back to.
    default_tax_rate: Decimal

    # -- every classifier the edit form can set, by code. Without these the
    # form's dropdowns have nothing to preselect: a coin already graded MS65
    # would show an empty Grade box, and saving from it would overwrite the
    # grade with nothing.
    item_kind: str | None = None
    country: str | None = None
    denomination: str | None = None
    bullion_form: str | None = None
    grade: str | None = None
    strike_type: str | None = None
    #: As collectors write it: MS65, PR69+. `grade` is the code alone.
    grade_display: str | None = None
    grade_designation: str | None = None
    grading_service: str | None = None
    metal: str | None = None
    series: str | None = None
    storage_form: str | None = None
    authenticity: str | None = None
    status: str | None = None
    disposition: str | None = None

    # -- the note's currency detail; all null for anything but a note.
    note_type: str | None = None
    seal_color: str | None = None
    fed_district: str | None = None
    signature_combination: str | None = None
    series_year: int | None = None
    series_letter: str | None = None
    serial_number: str | None = None
    #: The Friedberg number attached to the note, if any. Set through
    #: `POST`/`DELETE /inventory/{id}/friedberg`, never by a PATCH.
    friedberg_id: int | None = None
    friedberg_number: str | None = None
    #: unknown, proposed, confirmed or conflicting; None for anything but a note.
    friedberg_status: str | None = None
    #: Whether the attached catalogue row has been confirmed by anyone.
    friedberg_verified: bool | None = None

    #: Each field's most recent change by an edit, keyed by field: who made
    #: it and when (`item_field_change`). The editor names them when it warns
    #: about a field changed elsewhere. A field never edited has no entry.
    last_changes: dict[str, FieldChangeOut] = Field(default_factory=dict)

    #: What the item is beyond its grade: Star Note, No Motto, First Strike.
    #: Removed ones are not listed.
    attributes: list[ItemAttributeOut] = Field(default_factory=list)
    #: Why the item is up for sale, if it is: a save then needs
    #: `acknowledge_for_sale` (app.sale_state).
    sale_state: list[SaleUseOut] = Field(default_factory=list)


class InventoryItemUpdate(BaseModel):
    """A partial edit to an item, in the item's own vocabulary.

    Not `ListingUpdate` (`app.routers.offers`). That one speaks the shop's
    language -- a listing has a `title` and a `price`, meaning what the shop
    calls the item and what it is offered for. This one speaks the item's:
    `source_title` is what the row was called where it came from, and
    `item_cost` is what was paid for it. The Excel round trip uses these names
    too, so there is one vocabulary at this boundary rather than two.

    Every field optional, and applied with `exclude_unset`, so an omitted
    field is left alone rather than nulled.
    """

    #: The version read before editing. Send it and a conflicting save is a
    #: 409 rather than a silent overwrite; omit it to mean "set this
    #: regardless", which a script may legitimately want.
    version: int | None = None
    #: The value each field being changed had when the edit began, as
    #: `GET /inventory/{id}` returned it, keyed like the fields sent. With it
    #: a save is merged field by field: a change made elsewhere since then
    #: stops it only if it touched one of these fields (409 naming each, with
    #: both values), and `version` is not compared. Without it, `version`
    #: guards the whole item as before.
    base: dict[str, Any] | None = None

    source_title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = None
    year_start: int | None = Field(default=None, ge=-3000, le=2200)
    year_end: int | None = Field(default=None, ge=-3000, le=2200)
    fineness: Decimal | None = Field(default=None, ge=0, le=1, decimal_places=4)
    gross_weight_ozt: Decimal | None = Field(default=None, ge=0, decimal_places=6)
    fine_weight_ozt: Decimal | None = Field(default=None, ge=0, decimal_places=6)
    piece_count: int | None = Field(default=None, ge=1)
    item_cost: Decimal | None = Field(
        default=None, ge=Decimal("0"), max_digits=12, decimal_places=2
    )
    shipping_cost: Decimal | None = Field(
        default=None, ge=Decimal("0"), max_digits=12, decimal_places=2
    )
    #: A fraction, not a percentage: 0.0635 is 6.35%. Zero records a purchase
    #: that was charged no sales tax. Bounded, so 6.35 typed for 6.35% is a
    #: 422 rather than a cost multiplied by 7.35.
    tax_rate: Decimal | None = Field(
        default=None, ge=Decimal("0"), le=Decimal("1"), max_digits=6, decimal_places=4
    )
    tax_includes_shipping: bool | None = None

    # Classifiers, by code.
    item_kind: str | None = Field(default=None, max_length=64)
    country: str | None = Field(default=None, max_length=64)
    denomination: str | None = Field(default=None, max_length=64)
    bullion_form: str | None = Field(default=None, max_length=64)
    #: A number (65, 64+) or a compound grade (MS65, PR69+), which is split
    #: into the number and `strike_type`.
    grade: str | None = Field(default=None, max_length=64)
    strike_type: str | None = Field(default=None, max_length=64)
    grade_designation: str | None = Field(default=None, max_length=64)
    grading_service: str | None = Field(default=None, max_length=64)
    metal: str | None = Field(default=None, max_length=64)
    series: str | None = Field(default=None, max_length=64)
    storage_form: str | None = Field(default=None, max_length=64)
    authenticity: str | None = Field(default=None, max_length=64)
    status: str | None = Field(default=None, max_length=64)
    disposition: str | None = Field(default=None, max_length=64)

    # Banknote fields, on the note's currency detail. Refused by name for an
    # item that is not a note, as `ItemCreate` refuses them.
    note_type: str | None = Field(default=None, max_length=64)
    seal_color: str | None = Field(default=None, max_length=64)
    fed_district: str | None = Field(default=None, max_length=64)
    signature_combination: str | None = Field(default=None, max_length=64)
    series_year: int | None = Field(default=None, ge=1861, le=2200)
    series_letter: str | None = Field(default=None, max_length=4)
    serial_number: str | None = Field(default=None, max_length=64)

    #: Required, as true, to change an item that is up for sale: a listing
    #: offers it or an unshipped order holds it (app.sale_state).
    acknowledge_for_sale: bool = False

    #: The item's whole set of attribute codes, replacing what it carries.
    #: One the item had and this omits is marked removed, so no rule adds it
    #: back (app.item_attributes). Single-item edits only.
    attributes: list[str] | None = Field(default=None, max_length=64)

    @field_validator("attributes")
    @classmethod
    def _no_repeated_attribute(cls, codes: list[str] | None) -> list[str] | None:
        """The same attribute twice is a client mistake, not two facts."""
        if codes is not None and len(set(codes)) != len(codes):
            raise ValueError("each attribute may appear at most once")
        return codes


class BulkEditRequest(BaseModel):
    """One set of changes, applied to many items in one transaction.

    `changes` is validated as an `InventoryItemUpdate`, so bulk and single
    edits accept exactly the same fields and the same codes. Two field lists
    would drift.
    """

    #: At least one. "Apply to nothing" is far more likely a selection that
    #: was lost than something anyone meant.
    ids: list[int] = Field(min_length=1)
    changes: InventoryItemUpdate


#: Coin-detail fields on `ItemCreate`, valid only when `item_kind` is not
#: `currency`.
_COIN_ONLY_FIELDS: tuple[str, ...] = ("mint", "variety")

#: Currency-detail fields on `ItemCreate`, valid only when `item_kind` is
#: `currency`.
_CURRENCY_ONLY_FIELDS: tuple[str, ...] = (
    "serial_number",
    "series_year",
    "series_letter",
    "seal_color",
    "fed_district",
    "note_type",
    "signature_combination",
)


class ItemCreate(BaseModel):
    """A coin, banknote or other item bought on an existing purchase.

    This creates the item alone, with no listing, on a purchase order that
    must already exist -- entering what was bought, not putting it up for
    sale. Offering it later is `POST /api/offers` (`app.routers.offers`). See
    the decision in `docs/specs/entry-panels-design.md`: no item is entered
    outside a purchase, so a standalone buy is a purchase holding one item.
    """

    model_config = ConfigDict(extra="forbid")

    purchase_order_id: int
    item_kind: str = Field(min_length=1, max_length=64)
    source_title: str = Field(min_length=1, max_length=500)
    description: str = ""

    year_start: int | None = Field(default=None, ge=-3000, le=2200)
    year_end: int | None = Field(default=None, ge=-3000, le=2200)

    #: Pieces in the lot itself. A lot is simply piece_count > 1 -- splitting
    #: it into individually tracked pieces is a later step.
    piece_count: int = Field(default=1, ge=1)

    item_cost: Decimal = Field(
        default=Decimal("0.00"), ge=Decimal("0"), max_digits=12, decimal_places=2
    )
    shipping_cost: Decimal = Field(
        default=Decimal("0.00"), ge=Decimal("0"), max_digits=12, decimal_places=2
    )
    #: None means "use the configured default". Unlike `item_cost`, this has
    #: no default of its own: `Decimal("0")` is a real, different answer -- a
    #: purchase charged no sales tax at all -- so the caller must say so
    #: explicitly rather than the field quietly defaulting to it.
    tax_rate: Decimal | None = Field(
        default=None, ge=Decimal("0"), le=Decimal("1"), max_digits=6, decimal_places=4
    )
    #: None means "use the configured default", for the same reason as
    #: `tax_rate`.
    tax_includes_shipping: bool | None = None

    #: An `item_status` code. Only `ordered` or `received` describe an item
    #: as it is entered -- anything else this schema would have to accept and
    #: the router would then have to explain is wrong.
    status: str = "ordered"

    # Classifiers, by code. Unknown -> 422 naming the field, resolved by the
    # router since that is where the database lives.
    country: str | None = Field(default=None, max_length=64)
    denomination: str | None = Field(default=None, max_length=64)
    #: A number (65, 64+) or a compound grade (MS65, PR69+), which is split
    #: into the number and `strike_type`.
    grade: str | None = Field(default=None, max_length=64)
    strike_type: str | None = Field(default=None, max_length=64)
    grade_designation: str | None = Field(default=None, max_length=64)
    grading_service: str | None = Field(default=None, max_length=64)
    metal: str | None = Field(default=None, max_length=64)
    series: str | None = Field(default=None, max_length=64)
    bullion_form: str | None = Field(default=None, max_length=64)
    #: null -> `single`.
    storage_form: str | None = Field(default=None, max_length=64)
    #: null -> `unverified`.
    authenticity: str | None = Field(default=None, max_length=64)

    #: Creates one `item_certification` row, graded by `grading_service`.
    cert_number: str | None = Field(default=None, max_length=64)

    #: Coin detail. Refused with a 422 when `item_kind` is `currency`.
    mint: str | None = Field(default=None, max_length=64)
    variety: str | None = Field(default=None, max_length=128)

    #: Currency detail. Refused with a 422 for every `item_kind` but
    #: `currency`.
    serial_number: str | None = Field(default=None, max_length=64)
    series_year: int | None = Field(default=None, ge=-3000, le=2200)
    series_letter: str | None = Field(default=None, max_length=4)
    seal_color: str | None = Field(default=None, max_length=64)
    fed_district: str | None = Field(default=None, max_length=64)
    note_type: str | None = Field(default=None, max_length=64)
    signature_combination: str | None = Field(default=None, max_length=64)

    #: Fields whose value is a suggestion the form filled from the facts and
    #: the person left as it was, by field name. They are recorded as derived
    #: defaults; everything else sent is the person's.
    suggested: list[str] = Field(default_factory=list)

    @field_validator("status")
    @classmethod
    def _known_status(cls, value: str) -> str:
        """Only `ordered` or `received` describe an item as it is entered."""
        if value not in {"ordered", "received"}:
            raise ValueError(f"status must be 'ordered' or 'received', not {value!r}")
        return value

    @model_validator(mode="after")
    def _refuse_cross_kind_details(self) -> ItemCreate:
        """Refuse a detail field that belongs to the other kind, by name.

        A currency-detail field silently dropped for an item that turns out
        to be a coin, or the reverse, is data loss the caller is never told
        about -- a serial number that vanished rather than one that was
        refused. So this is a 422 naming exactly which field does not belong,
        not a silent no-op.
        """
        is_currency = self.item_kind == "currency"
        wrong_fields = _COIN_ONLY_FIELDS if is_currency else _CURRENCY_ONLY_FIELDS
        offending = sorted(f for f in wrong_fields if getattr(self, f) is not None)
        if offending:
            raise ValueError(
                f"{', '.join(offending)}: not valid for item_kind {self.item_kind!r}"
            )
        return self


class NoteSuggestionOut(BaseModel):
    """Classifier codes the facts decide for a note being entered; null if open."""

    note_type: str | None = None
    seal_color: str | None = None
    signature_combination: str | None = None
    fed_district: str | None = None


class CoinSuggestionOut(BaseModel):
    """The metal a coin's denomination, country and year decide; null if open."""

    metal: str | None = None


#: The four outcomes a receipt can record. `received` is the common one;
#: the rest close out a line that will not arrive. Without them there is no
#: way to finish an order except to leave it permanently outstanding.
RECEIVE_OUTCOMES: frozenset[str] = frozenset(
    {"received", "missing", "returned", "canceled"}
)


class ReceiveRequest(BaseModel):
    """One receipt, applied to one or many items at once."""

    item_ids: list[int] = Field(min_length=1)
    outcome: str
    arrived_on: date | None = None
    storage_location_id: int | None = None
    note: str | None = None
    #: Set after a refusal to say the caller knows an item is for sale
    #: (app.sale_state). Only the outcomes that are not `received` can be
    #: refused: an item that has not been received cannot be offered.
    acknowledge_for_sale: bool = False


class ReviewRequest(BaseModel):
    """Which fields of an item a person has confirmed by looking at it."""

    #: Column names, e.g. `grade_id`. Checked against the reviewable set, so a
    #: typo is a 422 rather than a record nobody can ever query for.
    fields: list[str] = Field(default_factory=list)
    #: False adds to what is already recorded, which is the normal case --
    #: confirming the grade says nothing about the year. True makes the given
    #: list the whole truth, which is how a mistaken confirmation is undone.
    replace: bool = False


class ItemReviewOut(BaseModel):
    """Which fields of one item stand confirmed."""

    inventory_item_id: int
    reviewed: list[str]


class ItemErrorIn(BaseModel):
    """One mint or printing error, as part of an item's whole set."""

    error_type: str = Field(max_length=64)
    #: Free text, per error -- so miscut and overprint on the same bill each
    #: get their own note rather than sharing one field.
    details: str | None = None


class ItemErrorsRequest(BaseModel):
    """The whole set of errors an item carries.

    A replace, not an add/remove pair: `PUT /inventory/{item_id}/errors`
    stores exactly this list and discards whatever was recorded before.
    """

    errors: list[ItemErrorIn] = Field(default_factory=list)
    #: Set after a refusal to say the caller knows the item is for sale
    #: (app.sale_state).
    acknowledge_for_sale: bool = False

    @field_validator("errors")
    @classmethod
    def _no_repeated_type(cls, errors: list[ItemErrorIn]) -> list[ItemErrorIn]:
        """The same error twice in one request is a client mistake, not two facts.

        Caught here rather than left to the database's unique constraint so
        the caller gets a 422 naming the repeat instead of a 500 from a bulk
        insert that half-applied.
        """
        seen = {e.error_type for e in errors}
        if len(seen) != len(errors):
            raise ValueError("each error_type may appear at most once per item")
        return errors


class ItemErrorOut(BaseModel):
    """One recorded error, as the API returns it."""

    error_type: str
    details: str | None = None
    source: str
    noted_by_id: int | None = None
    noted_at: datetime


class ItemErrorsOut(BaseModel):
    """Every error recorded against one item."""

    inventory_item_id: int
    errors: list[ItemErrorOut]


# --------------------------------------------------------------------------
# Inventory browse
# --------------------------------------------------------------------------


class FacetValueOut(BaseModel):
    """One value a filter could take, and how many rows would match it."""

    value: object
    count: int
    #: What a person reads: "Cent" for `usd_coin_0_01`. The filter still takes
    #: `value`. Absent where the value is already readable, as a series year is.
    label: str | None = None


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
    #: How many rows in this result set hit each named check. Populated when
    #: `facets=true`, and computed ignoring any `issue` filter so the sizes
    #: of the other jobs stay visible.
    issues: dict[str, int] = Field(default_factory=dict)
    #: What each check in `issues` means, written once here rather than
    #: copied into the client. `no_weight_bullion` does not explain itself
    #: from its code alone; this is what a chip's tooltip reads from.
    issue_descriptions: dict[str, str] = Field(default_factory=dict)
    #: The columns this view can be sorted by. The table makes only these
    #: headers clickable: every header used to look sortable while the server
    #: refused most of them, putting "cannot sort by" on the page instead.
    sortable: list[str] = Field(default_factory=list)


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
    #: Bounded to the column's own range: a larger number would overflow the
    #: INTEGER and surface as a 500 instead of a 422.
    sort_order: int | None = Field(default=None, ge=0, le=2**31 - 1)
    is_active: bool | None = None


class ReferenceMergeIn(BaseModel):
    """The value to merge into, and whether only to say what would happen."""

    into: str = Field(min_length=1, max_length=64)
    #: True: report what the merge would move, and change nothing.
    dry_run: bool = False
    #: Set after a refusal to say the caller knows some of the items the
    #: merge moves are for sale (app.sale_state).
    acknowledge_for_sale: bool = False


class ReferenceMergeOut(BaseModel):
    """What a merge moved, or would move."""

    table: str
    code: str
    into: str
    dry_run: bool
    #: Rows moved, by "table.column".
    moved: dict[str, int]
    #: Distinct items those rows describe.
    items: int
    #: Rows dropped because the item already had the kept value.
    dropped: int
    #: Names the kept value gains (or would gain).
    aliases: list[str]
    #: Item codes among those moved that are for sale, at most ten.
    for_sale: list[str] = Field(default_factory=list)
    #: How many are for sale in total, however many are named above.
    for_sale_count: int = 0


class ReferenceAliasIn(BaseModel):
    """Another name for a value: what people write instead of its label."""

    alias: str = Field(min_length=1, max_length=64)


# --------------------------------------------------------------------------
# Acquisitions: vendors, purchase orders and storage locations
# --------------------------------------------------------------------------

#: A web address is only ever `http://` or `https://` -- the same rule
#: `PurchaseOrderDetailOut.source_url` is filtered by before it is offered as
#: a link. Refused at entry rather than silently stored and withheld later,
#: since a value entered by hand (unlike imported spreadsheet text) is worth
#: telling the caller is wrong.
_HTTP_URL = re.compile(r"^https?://", re.IGNORECASE)


def _require_http_url(value: str | None) -> str | None:
    """Refuse a URL that is not `http://` or `https://`."""
    if value is not None and not _HTTP_URL.match(value):
        raise ValueError("must start with http:// or https://")
    return value


class VendorOut(BaseModel):
    """A vendor, for picking on the entry panels."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    url: str | None = None
    #: A `vendor_kind` code: marketplace, auction, mint, dealer, private,
    #: unknown.
    vendor_kind: str | None = None


class VendorCreate(BaseModel):
    """A vendor added inline while entering a purchase.

    Names are unique, checked case-insensitively -- "eBay.com" and "ebay.com"
    are refused as the same vendor rather than becoming two rows a report
    then has to know to merge.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    url: str | None = Field(default=None, max_length=500)
    #: A `vendor_kind` code. Null becomes `unknown`, the same fallback the
    #: importer uses for a vendor named with nothing else known about it.
    vendor_kind: str | None = Field(default=None, max_length=64)

    @field_validator("name")
    @classmethod
    def _trimmed_name(cls, value: str) -> str:
        """Leading and trailing whitespace is never part of a vendor's name."""
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("name must not be blank")
        return trimmed

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str | None) -> str | None:
        """Only `http://` or `https://`."""
        return _require_http_url(value)


_VENUE_CODE = r"^[a-z0-9][a-z0-9_-]*$"
_URL_PLACEHOLDER = "{external_id}"


def _template_has_placeholder(value: str | None) -> str | None:
    """A listing URL template must say where the listing number goes."""
    if value is not None and _URL_PLACEHOLDER not in value:
        raise ValueError(f"must contain {_URL_PLACEHOLDER}")
    return value


class SalesVenueOut(BaseModel):
    """A sales platform, for the Platforms page."""

    code: str
    name: str
    #: A `sales_venue_kind` code.
    kind: str
    is_own_store: bool
    vendor_id: int | None = None
    vendor_name: str | None = None
    account_handle: str | None = None
    listing_url_template: str | None = None
    commission_rate: Decimal | None = None
    processing_rate: Decimal | None = None
    processing_fixed: Decimal | None = None
    listing_fee: Decimal | None = None
    terms_as_of: date | None = None
    notes: str | None = None
    is_active: bool
    version: int


class _SalesVenueFields(BaseModel):
    """The editable fields a platform shares between create and update."""

    model_config = ConfigDict(extra="forbid")

    account_handle: str | None = Field(default=None, max_length=255)
    listing_url_template: str | None = Field(default=None, max_length=500)
    commission_rate: Decimal | None = Field(default=None, ge=0, le=1)
    processing_rate: Decimal | None = Field(default=None, ge=0, le=1)
    processing_fixed: Decimal | None = Field(default=None, ge=0)
    listing_fee: Decimal | None = Field(default=None, ge=0)
    terms_as_of: date | None = None
    notes: str | None = None
    vendor_id: int | None = None

    @field_validator("listing_url_template")
    @classmethod
    def _placeholder(cls, value: str | None) -> str | None:
        """The template names where the listing number goes."""
        return _template_has_placeholder(value)


class SalesVenueCreate(_SalesVenueFields):
    """A new platform. The web store already exists and cannot be added."""

    code: str = Field(min_length=1, max_length=64, pattern=_VENUE_CODE)
    name: str = Field(min_length=1, max_length=255)
    kind: str = Field(min_length=1, max_length=64)


class SalesVenueUpdate(_SalesVenueFields):
    """A change to a platform. Omitted fields are left alone; `code` is fixed."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    kind: str | None = Field(default=None, min_length=1, max_length=64)
    is_active: bool | None = None
    #: The version the form loaded; a mismatch is a 409.
    version: int | None = None


class OfferItemIn(BaseModel):
    """One item in an offer batch, and what it is offered for."""

    model_config = ConfigDict(extra="forbid")

    item_id: int
    #: Shaped like `listing.price`, `Numeric(12, 2)`. Without the precision
    #: limits a price of `10.005` is rounded by PostgreSQL on the way in and
    #: the response still reports what was sent, and twelve integer digits
    #: raise `DataError` -- which is not `IntegrityError` and is caught
    #: nowhere. Both become a 422 here instead.
    price: Decimal = Field(ge=Decimal("0"), max_digits=12, decimal_places=2)
    title: str = Field(default="", max_length=500)
    description: str = ""
    external_id: str | None = Field(default=None, max_length=128)


#: The lot half of `OfferIn`: what a lot listing is priced and worded as.
#: Meaningless on a batch of items, which carries a row per item instead, so
#: `OfferIn._one_subject` refuses them together rather than ignoring these.
_LOT_ONLY_FIELDS = ("price", "title", "description", "external_id")


class OfferIn(BaseModel):
    """Offer one or more items, **or** one assembled lot, on one platform.

    One platform and one format for the whole batch: offering the same five
    items half on eBay and half in the shop is two requests, and a batch that
    could span platforms would have to decide what a partial refusal means.

    A lot is one thing however many coins are in it, so a lot body carries
    one price, title, description and external id at the top level rather
    than a row per item. Exactly one of `items` and `lot_id`: passing both or
    neither is what keeps `offering_writes.offer`'s two `ValueError`s
    unreachable from outside this API, which `test_offering_writes.py`
    asserts is the case.
    """

    model_config = ConfigDict(extra="forbid")

    #: A `sales_venue` code.
    venue: str = Field(min_length=1, max_length=64)
    #: `fixed_price` or `auction`.
    format: str = "fixed_price"
    quantity: int = Field(default=1, ge=1)
    #: Capped because one batch is one transaction holding one `FOR UPDATE`
    #: row lock per item: an unbounded list is a single request that can lock
    #: every item in the table. 200 is well past any real screenful -- the
    #: console offers what an administrator has selected -- and far short of
    #: a lock set that would matter.
    #:
    #: No `min_length=1`: an empty list is one half of "neither", which
    #: `_one_subject` refuses by name. A `min_length` here would answer an
    #: empty *body* and an empty *lot* with the same 422, and a test that
    #: meant to prove the second would pass on the first.
    items: list[OfferItemIn] = Field(default_factory=list, max_length=200)
    #: The assembled lot to offer as one thing, instead of `items`.
    lot_id: int | None = None
    #: The lot's price. The precision limits of `OfferItemIn.price`, for the
    #: same two reasons. Required with `lot_id`, refused without it.
    price: Decimal | None = Field(
        default=None, ge=Decimal("0"), max_digits=12, decimal_places=2
    )
    #: The lot's wording, as `OfferItemIn` carries an item's.
    title: str = Field(default="", max_length=500)
    description: str = ""
    external_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _one_subject(self) -> OfferIn:
        """Exactly one subject, with only the fields that subject can use.

        A lot-only field sent alongside `items` is refused rather than
        ignored: silently dropping a price someone typed is the same data
        loss `ItemCreate._refuse_cross_kind_details` refuses above.

        `quantity` is refused rather than overridden for a lot.
        `ck_listing_lot_quantity_one` caps a lot listing at one unit and
        `offering_writes.offer` already forces it, so an explicit `2` would
        be accepted and quietly ignored -- the caller would believe two lots
        were on offer. Nothing could reach that override before this schema
        gained `lot_id`; now that a caller can, it is a 422 instead.
        """
        if self.lot_id is not None and self.items:
            raise ValueError("Offer items or a lot, not both")
        if self.lot_id is None and not self.items:
            raise ValueError("Offer at least one item, or a lot")
        if self.lot_id is not None:
            if self.price is None:
                raise ValueError("price is required when offering a lot")
            if self.quantity != 1:
                raise ValueError("a lot is one thing: quantity must be 1")
            return self
        sent = sorted(f for f in _LOT_ONLY_FIELDS if f in self.model_fields_set)
        if sent:
            raise ValueError(
                f"{', '.join(sent)}: belongs on each item of a batch, not on the batch"
            )
        return self


class OfferRefusalOut(BaseModel):
    """Why one item of a batch could not be offered."""

    item_code: str
    reason: str


class ListingOut(BaseModel):
    """One offer, for the owner's console. Admin-only: it carries cost basis.

    `external_url` is **computed for this response** from the platform's
    `listing_url_template` when the listing has an external id and no URL of
    its own; it is never written back to the row. Phase 1's design says the
    column holds what a person typed, and a derived value stored there would
    go stale the day a platform changes its URLs.

    **A listing offers an item or a lot, never both** (`ck_listing_item_xor_lot`),
    so the two pairs below are mutually exclusive and both are optional. They
    were required until lots existed, and `routers/offers._out` read
    `listing.inventory_item.item_code` unconditionally: the moment a lot
    listing was written, `GET /api/listings` raised `AttributeError` for
    every administrator -- a 500 on the page that lists every offer.
    """

    id: int
    #: Null on a lot listing; `sales_lot_id` is filled instead.
    item_id: int | None = None
    item_code: str | None = None
    #: The item's `source_title`, or the **lot's** title for a lot listing:
    #: one field the console can always show, whichever kind this is.
    item_title: str
    #: A `sales_venue` code.
    venue: str
    venue_name: str
    format: str
    status: str
    price: Decimal
    currency: str
    quantity_available: int
    title: str
    description: str
    external_id: str | None = None
    external_url: str | None = None
    listed_at: datetime
    ended_at: datetime | None = None
    paused_by_listing_id: int | None = None
    #: Null on an item listing; the lot being offered, for a lot listing.
    sales_lot_id: int | None = None
    #: How many coins that lot holds, so the Listings page can say "3 items"
    #: without a request per row. Null on an item listing, which is one.
    member_count: int | None = None
    #: `inventory_item.total_cost`, or the sum of the members' for a lot.
    #: Staff-only, and never added to `CatalogItemOut`, which a customer
    #: reads.
    cost_basis: Decimal | None = None
    #: Send this back on a PATCH to be told about a conflicting edit. An
    #: `int`, like `SalesVenueOut.version` and unlike `CatalogItemOut.version`
    #: -- that one is a composite token over two separately versioned rows,
    #: while this is one `listing.version` column and nothing else.
    version: int


class OfferBatchOut(BaseModel):
    """What a successful offer batch wrote."""

    listings: list[ListingOut]


class OfferTitlesOut(BaseModel):
    """A suggested public title per item id (`app.offer_titles`).

    Ids that do not exist are absent rather than an error: the dialog asking
    is only filling in defaults, and keeps the wording it already has.
    """

    titles: dict[int, str]


class OfferRefusedOut(BaseModel):
    """The 409 body when a batch is refused: nothing was written."""

    detail: str
    refused: list[OfferRefusalOut]


class ListingUpdate(BaseModel):
    """A change to an offer. Omitted fields are left alone."""

    model_config = ConfigDict(extra="forbid")

    #: The precision limits of `OfferItemIn.price`, for the same two reasons.
    price: Decimal | None = Field(
        default=None, ge=Decimal("0"), max_digits=12, decimal_places=2
    )
    title: str | None = Field(default=None, max_length=500)
    description: str | None = None
    external_id: str | None = Field(default=None, max_length=128)
    #: The version the form loaded; a mismatch is a 409.
    version: int | None = None


# --------------------------------------------------------------------------
# Sales lots
# --------------------------------------------------------------------------


class SalesLotIn(BaseModel):
    """Start a lot. It begins `assembling` and empty; members are a PATCH."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)
    description: str = ""


class SalesLotUpdate(BaseModel):
    """A change to an assembling lot: its wording, its membership, or both.

    One endpoint rather than three, because the three are one edit as far as
    the person assembling a lot is concerned, and splitting them would give
    a screenful of changes three chances to lose a race instead of one.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    #: Capped for the reason `OfferIn.items` is: one request is one
    #: transaction holding one row lock per item named here.
    add_item_ids: list[int] = Field(default_factory=list, max_length=200)
    remove_item_ids: list[int] = Field(default_factory=list, max_length=200)
    #: The version the form loaded; a mismatch is a 409.
    version: int | None = None


class SalesLotMemberOut(BaseModel):
    """One coin in a lot, with the two staff-only figures a lot is judged on."""

    inventory_item_id: int
    item_code: str
    title: str
    #: `inventory_item.total_cost`. A `Decimal` field, so it crosses the wire
    #: as a string; the same figure in a plain `dict` would be a float.
    cost_basis: Decimal
    #: `inventory_item.numismatic_value`, the hand-entered collector premium.
    #: Null when nobody has valued this coin yet.
    value: Decimal | None = None


class SalesLotOut(BaseModel):
    """One lot for the console. Admin-only: the figures below are staff-only.

    `cost_basis` and `value` are running totals over the members, which is
    what an owner assembling a lot is watching -- what the group cost against
    what it is thought to be worth, as coins go in and out.
    """

    id: int
    title: str
    description: str
    #: `assembling`, `offered`, `sold` or `dissolved`.
    status: str
    version: int
    members: list[SalesLotMemberOut]
    #: The members' `total_cost`, summed.
    cost_basis: Decimal
    #: The members' `numismatic_value`, summed. An unvalued member
    #: contributes nothing, so this is a floor rather than an estimate --
    #: `unvalued_count` says how many coins are missing from it.
    value: Decimal
    unvalued_count: int


class SalesLotListOut(BaseModel):
    """One page of the lots the filter matched, and how many it matched."""

    lots: list[SalesLotOut]
    #: Every lot the filter matched, not only this page's.
    total: int


class FeeLineIn(BaseModel):
    """One actual fee from the platform's statement."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    #: The precision limits of `OfferItemIn.price`, for the same two reasons:
    #: `max_digits` alone admits `1E+11`, which passes `record_sale`'s own
    #: check and then overflows `sales_order_fee.amount`'s `Numeric(12, 2)`
    #: as a `DataError` PostgreSQL raises, not pydantic -- `decimal_places`
    #: must be given alongside `max_digits` for the ten-whole-digit limit to
    #: apply at all. `ge=0` closes the same gap for sign: `record_sale`
    #: checks a fee's sign itself too, for phase-4 auction settlement, but
    #: this schema is what keeps a negative fee from ever reaching that
    #: check by way of an `IntegrityError` on `ck_sales_order_fee_non_negative`.
    amount: Decimal = Field(ge=Decimal("0"), max_digits=12, decimal_places=2)
    note: str | None = Field(default=None, max_length=255)


class RecordSaleIn(BaseModel):
    """A sale that happened on an outside platform, entered after the fact."""

    model_config = ConfigDict(extra="forbid")

    #: Same bound as `FeeLineIn.amount`, for the same reason.
    price: Decimal = Field(ge=Decimal("0"), max_digits=12, decimal_places=2)
    buyer_username: str | None = Field(default=None, max_length=128)
    external_order_id: str | None = Field(default=None, max_length=128)
    fees: list[FeeLineIn] = Field(default_factory=list)
    equal_shares: bool = False


class SaleRecordedOut(BaseModel):
    """What was recorded, with the arithmetic the console shows back."""

    id: int
    external_order_id: str | None
    total_amount: Decimal
    fee_total: Decimal
    net_amount: Decimal
    buyer: str
    item_codes: list[str]


# --------------------------------------------------------------------------
# Auctions
# --------------------------------------------------------------------------


class AuctionIn(BaseModel):
    """Start an auction. It begins `draft`, with no lots."""

    model_config = ConfigDict(extra="forbid")

    #: A `sales_venue` code.
    venue: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    #: The house's own number for the sale.
    external_id: str | None = Field(default=None, max_length=128)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    notes: str | None = None


class AuctionUpdate(BaseModel):
    """A change to an auction's own wording or dates. Omitted fields are left alone.

    Never the platform: an auction's lots are already offered on
    `auction.sales_venue`, and changing it here would desync them from the
    listings `add_lot` created against the venue this row named at the time.
    Never the status either -- each transition is its own endpoint, because
    moving from one status to the next has consequences a field assignment
    cannot express.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=255)
    external_id: str | None = Field(default=None, max_length=128)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    notes: str | None = None
    #: The version the form loaded; a mismatch is a 409.
    version: int | None = None


class AuctionLotIn(BaseModel):
    """Add a lot to an auction: an assembled lot, or a single item as a lot of one.

    Exactly one of `item_id` and `lot_id` -- the same discipline `OfferIn`
    holds `items` and `lot_id` to, and for the same reason: a request naming
    both or neither is ambiguous rather than merely redundant.
    """

    model_config = ConfigDict(extra="forbid")

    lot_number: str = Field(min_length=1, max_length=32)
    item_id: int | None = None
    lot_id: int | None = None
    reserve: Decimal | None = Field(
        default=None, ge=Decimal("0"), max_digits=12, decimal_places=2
    )
    #: The starting bid. Defaults to 0 when omitted, `add_lot`'s own meaning
    #: for "none given".
    price: Decimal | None = Field(
        default=None, ge=Decimal("0"), max_digits=12, decimal_places=2
    )
    title: str | None = Field(default=None, max_length=500)
    description: str | None = None
    external_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _one_subject(self) -> AuctionLotIn:
        """Exactly one of `item_id`, `lot_id` -- never both, never neither."""
        if (self.item_id is None) == (self.lot_id is None):
            raise ValueError("Exactly one of item_id or lot_id is required")
        return self


class AuctionLotUpdate(BaseModel):
    """Renumber a lot, or change its reserve. Omitted fields are left alone."""

    model_config = ConfigDict(extra="forbid")

    lot_number: str | None = Field(default=None, min_length=1, max_length=32)
    reserve: Decimal | None = Field(
        default=None, ge=Decimal("0"), max_digits=12, decimal_places=2
    )


class AuctionConsignIn(BaseModel):
    """Mark an auction consigned: when custody moved to the house."""

    model_config = ConfigDict(extra="forbid")

    on_date: date


class AuctionCancelIn(BaseModel):
    """Cancel an auction, and where its consigned items come back to, if any."""

    model_config = ConfigDict(extra="forbid")

    returned_to_location_id: int | None = None


class AuctionLotOut(BaseModel):
    """One lot in an auction: its slot in the sale, and the listing behind it."""

    id: int
    lot_number: str
    reserve: Decimal | None = None
    #: `sold`, `unsold` or `withdrawn`; null until the auction is settled.
    result: str | None = None
    hammer_price: Decimal | None = None
    #: The buyer's display name, once the lot has sold. Null before
    #: settlement, and for an auction house that never named one.
    buyer: str | None = None
    listing: ListingOut


class AuctionOut(BaseModel):
    """One auction, with every lot it currently holds."""

    id: int
    #: A `sales_venue` code.
    venue: str
    venue_name: str
    title: str
    external_id: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    status: str
    #: When custody moved to the house. Null unless it is, or was, consigned.
    consigned_on: date | None = None
    notes: str | None = None
    #: Send this back on a PATCH to be told about a conflicting edit.
    version: int
    lots: list[AuctionLotOut]


class AuctionListOut(BaseModel):
    """Every auction the filter matched. An object, so a page count can be added."""

    auctions: list[AuctionOut]


class SettlementLineIn(BaseModel):
    """One lot's outcome, as the settlement grid enters it.

    `hammer_price` and `buyer_username` matter only when `result` is `sold`;
    `app.auctions.settle` refuses either one given alongside an `unsold` or
    `withdrawn` result.
    """

    model_config = ConfigDict(extra="forbid")

    auction_lot_id: int
    #: `sold`, `unsold` or `withdrawn`.
    result: str
    #: Same precision limits as `OfferItemIn.price`, for the same two reasons.
    hammer_price: Decimal | None = Field(
        default=None, ge=Decimal("0"), max_digits=12, decimal_places=2
    )
    buyer_username: str | None = Field(default=None, max_length=128)


class SettlementFeesIn(BaseModel):
    """One buyer's actual fees from the house's statement.

    `buyer_username=None` is the auction house's standing undisclosed buyer
    -- the same meaning `SettlementLineIn.buyer_username=None` carries --
    never a blank the owner still has to fill in; the house's own statement
    either names a buyer or it does not.
    """

    model_config = ConfigDict(extra="forbid")

    buyer_username: str | None = Field(default=None, max_length=128)
    fees: list[FeeLineIn] = Field(default_factory=list)


class SettleIn(BaseModel):
    """A whole settlement grid: every lot's result, and every buyer's fees."""

    model_config = ConfigDict(extra="forbid")

    #: Capped for the reason `OfferIn.items` is: one request is one
    #: transaction holding one row lock per item the auction's lots name.
    #:
    #: No `min_length=1`: an empty grid is `app.auctions.settle`'s own
    #: refusal to make, naming every lot that has no result -- the same
    #: reason `OfferIn.items` carries no minimum either.
    lines: list[SettlementLineIn] = Field(default_factory=list, max_length=500)
    fees: list[SettlementFeesIn] = Field(default_factory=list, max_length=200)
    #: Where a house's unsold and withdrawn lots come back to. Required only
    #: when the house still holds something and at least one lot is coming
    #: back -- `app.auctions.settle` decides that, not this schema.
    returned_to_location_id: int | None = None

    @model_validator(mode="after")
    def _refuse_duplicate_fee_buyers(self) -> SettleIn:
        """Refuse two fee groups naming the same `buyer_username` exactly.

        Two groups whose spelling merely *casefolds* to the same buyer --
        `CoinFan88` and `coinfan88` -- are `app.auctions.settle`'s own job to
        catch and name (ruling R17): each survives into the `Mapping` this
        schema builds, because a `dict` keyed on the literal strings keeps
        both. An **exact** repeat of one spelling would not: building that
        `Mapping` from this list would let the second entry silently
        overwrite the first, which is the "a lookup miss must not default
        silently" hazard for a write instead of a read. Caught here, before
        that `Mapping` is ever built, rather than let one buyer's fees
        vanish.
        """
        seen: set[str | None] = set()
        duplicated: list[str] = []
        for group in self.fees:
            if group.buyer_username in seen:
                duplicated.append(group.buyer_username or "the undisclosed buyer")
            seen.add(group.buyer_username)
        if duplicated:
            raise ValueError(
                f"fees given twice for the same buyer_username: {', '.join(duplicated)}"
            )
        return self


class SettleOut(BaseModel):
    """What a settlement wrote: the auction as it now stands, and every order."""

    auction: AuctionOut
    orders: list[SaleRecordedOut]


class AuctionRefusalOut(BaseModel):
    """One problem a refused auction transition named, as `app.auctions.AuctionRefusal`.

    `lot_number` is set when the problem names one lot in particular --
    most of them do -- and null for one that does not (a buyer's fees, or
    the auction as a whole), or for a refusal from `sales_writes` rather
    than `app.auctions`, which carries no lot at all.
    """

    reason: str
    lot_number: str | None = None


class AuctionRefusedOut(BaseModel):
    """The 409 or 422 body an `AuctionRefused`-family exception produces.

    `refused` always holds at least one entry: most transitions refuse for
    one reason, and `settle` may refuse for several. Ruling R21 (Task 5
    follow-up): built directly from the raised exception's own `refusals`
    attribute (`app.auctions.AuctionRefused.refusals`, a list of
    `app.auctions.AuctionRefusal`) rather than by splitting `str(exc)` on
    `"; "` -- a text convention that broke the moment a problem's own words
    contained a semicolon, and that nothing type-checked. A
    `sales_writes.SaleRefused`/`SaleInputInvalid`, which carries no such
    attribute, still produces a `refused` list of exactly one entry, built
    from its plain message.
    """

    detail: str
    refused: list[AuctionRefusalOut]


class PurchaseOrderCreate(BaseModel):
    """A new purchase: a vendor, and everything else optional.

    The order number is optional because a walk-in or show purchase has
    none -- only a vendor is required, so entering one never blocks on a
    field most purchases genuinely lack.
    """

    model_config = ConfigDict(extra="forbid")

    vendor_id: int
    order_number: str | None = Field(default=None, max_length=128)
    #: Not in the future beyond today + 1 day -- the same bound
    #: `POST /api/inventory/receive` applies to `arrived_on`, and for the
    #: same reason: a caller's local "today" can be a day ahead of UTC's.
    ordered_on: date | None = None
    source_url: str | None = Field(default=None, max_length=1000)
    notes: str | None = None

    @field_validator("order_number")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        """An empty order number is the same absence as none at all."""
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None

    @field_validator("source_url")
    @classmethod
    def _http_url(cls, value: str | None) -> str | None:
        """Only `http://` or `https://`, as `PurchaseOrderDetailOut` requires."""
        return _require_http_url(value)


class PurchaseOrderOut(BaseModel):
    """One acquisition, with how much of it is still outstanding.

    `outstanding` and `total` are line counts, not dollar amounts: this is
    the row a receiving list shows before anyone opens the order.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    order_number: str | None
    vendor: str
    ordered_on: date | None
    outstanding: int
    total: int


class PurchaseOrderLineOut(BaseModel):
    """One line of a purchase order: an item and where it stands."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    item_code: str
    #: The item's own title -- what `NewPurchase`'s items table shows, and
    #: what an entry form's Title box wrote. `description` is kept alongside
    #: it because older, imported lines can have a title-less description.
    source_title: str
    description: str
    #: An `item_kind` code: coin, currency, bullion, set, medal, token, other.
    item_kind: str
    item_cost: Decimal
    #: An `item_status` code: ordered, received, canceled, returned, missing.
    status: str


class PurchaseOrderDetailOut(BaseModel):
    """A purchase order and every line acquired on it."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    order_number: str | None
    vendor: str
    ordered_on: date | None
    #: The vendor's page for the order or listing, when it is a web address.
    #: Imported spreadsheet text, so anything else -- "Gift" -- is withheld
    #: rather than offered as a link.
    source_url: str | None = None
    lines: list[PurchaseOrderLineOut]


class StorageLocationOut(BaseModel):
    """Where an item physically sits. Admin-only: never customer-visible."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    label: str
    #: A `storage_location_kind` code: safe_deposit_box, safe, home, ...
    kind: str


# --------------------------------------------------------------------------
# Friedberg numbers: the owner's own catalogue, not a licensed dataset
# --------------------------------------------------------------------------


class FriedbergNumberOut(BaseModel):
    """One row of the owner's Friedberg catalogue.

    `verified` mirrors whether `verified_at` is set -- a confirmed row is a
    fact the next lookup can trust, a proposal is not.
    """

    id: int
    fr_number: str
    note_type: str | None
    denomination: str | None
    series_year: int | None
    series_letter: str | None
    seal_color: str | None
    signature_combination: str | None
    district_letter: str | None
    size_class: str | None
    #: Printed on a web press; None when not known.
    web_press: bool | None
    description: str | None
    #: A `provenance_source` value: seeded, derived, manual. Every row here is
    #: `manual` today -- there is no licensed dataset to seed from -- but the
    #: field is carried through so a later merge stays distinguishable.
    source: str
    verified: bool
    verified_at: datetime | None


class FriedbergNumberCreate(BaseModel):
    """A Friedberg number read off a note or slab in the owner's hands.

    The attribute tuple is the same one `GET /friedberg` filters on, and every
    field but `fr_number` is optional -- a half-known type is a normal state
    for a catalogue built by hand as notes arrive.
    """

    fr_number: str = Field(min_length=1, max_length=32)
    note_type: str | None = None
    denomination: str | None = None
    series_year: int | None = Field(default=None, ge=1861, le=2100)
    series_letter: str | None = Field(default=None, max_length=4)
    seal_color: str | None = None
    signature_combination: str | None = None
    district_letter: str | None = Field(default=None, min_length=1, max_length=1)
    size_class: str | None = Field(default=None, pattern="^(large|small|fractional)$")
    web_press: bool | None = None
    description: str | None = None


class SignatureChoice(BaseModel):
    """One Treasurer / Secretary pair the lookup may offer."""

    code: str
    label: str


class SignatureChoicesOut(BaseModel):
    """The signature pairs a note of the given series can carry.

    `source` says how sure the list is: `note_issue` is the seeded facts for
    that series, `term` is the fallback for a series with none (every pair
    still in office at or after the series year), and `all` means nothing
    was given to narrow by.
    """

    values: list[SignatureChoice]
    source: str


class FriedbergAttachIn(BaseModel):
    """Attach a catalogue row to a currency item's `currency_detail`."""

    friedberg_id: int
    #: A `friedberg_status` value: unknown, proposed, confirmed, conflicting.
    status: str


class FriedbergAttachOut(BaseModel):
    """The result of attaching a Friedberg number to an item."""

    inventory_item_id: int
    friedberg_id: int
    friedberg_status: str
    fr_number: str
    verified: bool
    verified_at: datetime | None
