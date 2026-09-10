"""Core tables: the acquisition side and the inventory spine.

`inventory_item` is the single table every other concern hangs off. Coins and
currency share it rather than splitting into parallel tables: they share
purchase, cost, grade, certification, status, location and images; acquisitions
routinely contain both; sales must reference either through one foreign key;
and an item whose kind is not yet determined still needs somewhere to live.
The attributes that genuinely differ live in 1:1 detail tables.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Sequence,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from ..config import settings
from .base import Base, ProvenanceSource, TimestampMixin, enum_column

if TYPE_CHECKING:  # relationship targets only -- importing these at runtime
    # would make core, images and sales import one another in a cycle.
    from .images import ItemImage
    from .sales import Listing
from .reference import (
    Authenticity,
    BullionForm,
    Country,
    Denomination,
    Disposition,
    Grade,
    GradeDesignation,
    GradingService,
    ItemKind,
    ItemStatus,
    Metal,
    SetForm,
    StorageForm,
)

__all__ = [
    "CoinDetail",
    "CurrencyDetail",
    "InventoryItem",
    "PurchaseOrder",
    "Vendor",
]

#: `sales_tax` and `total_cost` are both generated. PostgreSQL forbids a
#: generated column from referencing another generated column, so the tax
#: expression is repeated inside the total rather than referenced -- keeping
#: them as one constant here means the two can never drift apart.
#:
#: Both inputs that decide the tax -- the rate, and whether shipping is taxed
#: -- are columns on the row: not literals, and not settings read at query
#: time. See `_configured_tax_rate` for why.
_TAX_EXPR = (
    "round((item_cost + CASE WHEN tax_includes_shipping THEN shipping_cost "
    "ELSE 0 END) * tax_rate, 2)"
)


def _configured_tax_rate() -> Decimal:
    """The rate a new item is stamped with: the setting as it stands now.

    Called once, on INSERT, and never again. Tax paid is a historical fact, so
    the setting is copied onto the row rather than read when tax is computed:
    changing SALES_TAX_RATE then governs purchases recorded after the change
    and rewrites none recorded before it.
    """
    return settings.sales_tax_rate


def _configured_tax_includes_shipping() -> bool:
    """Whether a new item's shipping is taxed, per the setting as it stands now.

    Stamped on INSERT for the same reason as `_configured_tax_rate`.
    """
    return settings.sales_tax_includes_shipping


#: Prefix for the permanent item code. Fixed at migration time because it is
#: baked into a column default; changing it later renames nothing already
#: issued, which is correct -- codes are permanent.
ITEM_CODE_PREFIX = "CC"

#: The sequence that issues item codes. Registered on the metadata so that a
#: `create_all` test database gets it too, not only a migrated one.
item_code_sequence = Sequence("item_code_seq", start=1, metadata=Base.metadata)

#: Assigned by the database, not by the application: two concurrent inserts
#: must not be able to receive the same code, and a sequence is the only thing
#: that guarantees it without a lock.
#:
#: Written exactly as PostgreSQL stores it, casts and parentheses included --
#: the same reason as the full-text index below. PostgreSQL normalises a
#: default expression on creation, so the shorter form reflects back
#: differently and autogenerate reports the column as changed on every run.
ITEM_CODE_DEFAULT = (
    f"('{ITEM_CODE_PREFIX}-'::text || "
    "lpad((nextval('item_code_seq'::regclass))::text, 6, '0'::text))"
)


# Foreign-key target: a string, so SQLAlchemy resolves it at
# mapper-configuration time rather than forcing an import.
_FK_INVENTORY_ITEM = "inventory_item.id"

# Delete children with the parent, and any child removed from the collection.
_CASCADE_ALL_DELETE_ORPHAN = "all, delete-orphan"

# Predicate shared by every partial facet index below: a split item is
# superseded by its children and must not be counted twice.
_LIVE_ROWS_ONLY = "split_at IS NULL"


class Vendor(TimestampMixin, Base):
    """Where items are acquired from."""

    __tablename__ = "vendor"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    host: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    vendor_kind_id: Mapped[int | None] = mapped_column(
        ForeignKey("vendor_kind.id", ondelete="RESTRICT"), index=True, nullable=True
    )

    orders: Mapped[list[PurchaseOrder]] = relationship(back_populates="vendor")

    __table_args__ = (UniqueConstraint("name", name="uq_vendor_name"),)


class PurchaseOrder(TimestampMixin, Base):
    """One acquisition, commonly containing many items.

    ``order_number`` is **text**, always. Identifiers from marketplaces and
    auction houses contain leading zeros, letters and separators; storing them
    as a number destroys information irreversibly. Many channels issue no order
    number at all, hence nullable with a partial unique index.
    """

    __tablename__ = "purchase_order"

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_id: Mapped[int] = mapped_column(
        ForeignKey("vendor.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    order_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ordered_on: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    vendor: Mapped[Vendor] = relationship(back_populates="orders")
    items: Mapped[list[InventoryItem]] = relationship(back_populates="purchase_order")

    __table_args__ = (
        Index(
            "uq_purchase_order_vendor_number",
            "vendor_id",
            "order_number",
            unique=True,
            postgresql_where=text("order_number IS NOT NULL"),
        ),
    )


class InventoryItem(TimestampMixin, Base):
    """One acquired item or lot -- the shared spine of the whole schema."""

    __tablename__ = "inventory_item"

    #: Optimistic concurrency. Incremented on every ORM update, and the UPDATE
    #: carries `WHERE version = <the one that was read>`; if another writer got
    #: there first the statement matches no rows and SQLAlchemy raises.
    #:
    #: This is the protection PostgreSQL's MVCC does not give. MVCC makes
    #: readers and writers never block each other, but a read and a write in
    #: *different* transactions -- which is what an edit form is -- can still
    #: lose an update: two people load the same item, both save, and the second
    #: silently overwrites the first with values it loaded before the change.
    #:
    #: Optimistic rather than a lock, because editing an item is a document
    #: edit: conflicts are rare, they can take minutes of human thinking time,
    #: and when one happens a person can resolve it. Holding a row lock across
    #: that would block every reader of the row for as long as the form is
    #: open. Contrast `create_order`, which decrements a counter and uses
    #: SELECT ... FOR UPDATE -- there, conflicts are the normal case and there
    #: is nothing for a human to merge.
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )

    # A directive rather than a dict literal: see the note on __tablename__
    # in base.py. ClassVar conflicts with DeclarativeBase's declaration and
    # Final is a Liskov violation over it, while a bare literal trips
    # RUF012. A directive is none of those, and matches __table_args__.
    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:
        """Optimistic concurrency: every UPDATE checks the version it read."""
        return {"version_id_col": cls.version}

    id: Mapped[int] = mapped_column(primary_key=True)

    # -- acquisition ------------------------------------------------------
    purchase_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("purchase_order.id", ondelete="RESTRICT"),
        index=True,
        nullable=True,
    )

    # -- classification ---------------------------------------------------
    item_kind_id: Mapped[int] = mapped_column(
        ForeignKey("item_kind.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    denomination_id: Mapped[int | None] = mapped_column(
        ForeignKey("denomination.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    bullion_form_id: Mapped[int | None] = mapped_column(
        ForeignKey("bullion_form.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    set_form_id: Mapped[int | None] = mapped_column(
        ForeignKey("set_form.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    storage_form_id: Mapped[int] = mapped_column(
        ForeignKey("storage_form.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    #: How many pieces this row stands for. A multi-piece row stays one row:
    #: it was bought as a lot and is stored as a lot.
    #:
    #: Named `piece_count` rather than `storage_quantity` because it is not a
    #: fact about storage -- `storage_form` and `storage_location` answer
    #: that. It is how many objects the row represents, which is what every
    #: weight and valuation multiplies by.
    piece_count: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1"), nullable=False
    )
    country_id: Mapped[int | None] = mapped_column(
        ForeignKey("country.id", ondelete="RESTRICT"), index=True, nullable=True
    )

    #: A range, because a mint set or a roll spans several years.
    year_start: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    year_end: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # -- condition --------------------------------------------------------
    grade_id: Mapped[int | None] = mapped_column(
        ForeignKey("grade.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    grade_designation_id: Mapped[int | None] = mapped_column(
        ForeignKey("grade_designation.id", ondelete="RESTRICT"),
        index=True,
        nullable=True,
    )
    grading_service_id: Mapped[int | None] = mapped_column(
        ForeignKey("grading_service.id", ondelete="RESTRICT"),
        index=True,
        nullable=True,
    )
    authenticity_id: Mapped[int] = mapped_column(
        ForeignKey("authenticity.id", ondelete="RESTRICT"), index=True, nullable=False
    )

    # Mint and printing errors live in `item_error`, not here: miscut and
    # overprint commonly appear on the same bill, and a single FK cannot
    # express that. See `app.models.identification.ItemError`.

    # -- lifecycle, two independent axes ----------------------------------
    status_id: Mapped[int] = mapped_column(
        ForeignKey("item_status.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    disposition_id: Mapped[int] = mapped_column(
        ForeignKey("disposition.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    storage_location_id: Mapped[int | None] = mapped_column(
        ForeignKey("storage_location.id", ondelete="RESTRICT"),
        index=True,
        nullable=True,
    )

    # -- identity ---------------------------------------------------------
    #: The permanent identifier for this physical object.
    #:
    #: Assigned once, never reused, never changed. It survives everything that
    #: happens to the item: listed, sold, returned by the buyer, relisted. That
    #: continuity is the point -- a returned item must resume its own history
    #: rather than start a new one, and an audit has to be able to follow one
    #: object from acquisition through to disposal.
    #:
    #: Drawn from a sequence rather than derived from `id`, so it stays stable
    #: if rows are ever migrated or renumbered, and so gaps left by deletions
    #: are never filled by a later item wearing a dead item's code.
    #:
    #: Short enough to write on a flip or a box label by hand.
    item_code: Mapped[str] = mapped_column(
        String(32),
        server_default=text(ITEM_CODE_DEFAULT),
        nullable=False,
    )

    # -- lineage ----------------------------------------------------------
    #: The lot this piece was broken out of, if it was.
    #:
    #: A tube of twenty rounds or a mint set is bought as one thing and may
    #: later be sold as many. Splitting creates a child per piece and points
    #: it here, so the cost basis of every piece can be traced back to the
    #: purchase it actually came from -- which is the whole requirement for a
    #: defensible gain calculation years later.
    #: The design series -- Morgan Dollar, Winged Liberty Head Dime.
    #:
    #: On the item rather than on `coin_detail` so that faceting groups by an
    #: indexed foreign key on the table already being scanned. Measured
    #: earlier: grouping by a joined column was most of the cost of the whole
    #: search. It also lets a banknote carry a series without a second path.
    series_id: Mapped[int | None] = mapped_column(
        ForeignKey("series.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    parent_item_id: Mapped[int | None] = mapped_column(
        ForeignKey(_FK_INVENTORY_ITEM, ondelete="RESTRICT"),
        index=True,
        nullable=True,
    )
    #: Set on the *parent* when it is broken up.
    #:
    #: The parent row is kept rather than deleted: it holds the purchase
    #: order, the original price and the item code that a receipt or an
    #: invoice refers to. But it is no longer a thing anyone holds, so
    #: anything that counts inventory or money must exclude it -- otherwise
    #: the lot and its pieces are both counted and the collection appears to
    #: cost twice what it did.
    split_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    #: Soft delete: this row should never have existed.
    #:
    #: Not a `disposition` value. Disposition records what happened to a coin
    #: -- held, listed, sold, shipped -- and "created by mistake" is not
    #: something that happened to a coin. Putting it there would corrupt every
    #: disposition report with rows that were never real.
    #:
    #: A column also lets `WHERE deleted_at IS NULL` sit beside the
    #: `split_at IS NULL` already in all four views: same shape of rule, same
    #: place.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )

    # -- description ------------------------------------------------------
    #: A number the owner assigned by their own scheme, before this system
    #: existed. Distinct from `item_code`: not unique, not issued here, and
    #: kept only so their old references still resolve.
    local_catalog_number: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    #: Whatever the source called this row. The spreadsheet's leftmost
    #: column was the denomination, so this holds "Rolls .25", "$20 Bill",
    #: "Duit" and "2" -- not a name. `description` is what a person
    #: recognises an item by; this is kept because it is what the source
    #: said, and discarding a source value is not this project's habit.
    source_title: Mapped[str] = mapped_column(
        String(500), default="", server_default=text("''"), nullable=False
    )
    description: Mapped[str] = mapped_column(
        Text, default="", server_default=text("''"), nullable=False
    )
    listing_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # -- verbatim source text ---------------------------------------------
    # A parser can be wrong or incomplete, so nothing it read is discarded.
    notes_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    denom_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    year_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    grade_raw: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- cost basis, fixed at purchase ------------------------------------
    #: What was paid for the item itself. Named `item_cost` rather than
    #: `price` because `listing.price` is what it is *offered* for, and one
    #: word for both sides of a transaction is a confusion waiting to be
    #: made -- especially in a report that joins them.
    item_cost: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00"), nullable=False
    )
    #: Inbound shipping, paid on acquisition. The sales side has its own
    #: outbound shipping, which is a different number.
    shipping_cost: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00"), nullable=False
    )
    #: A fraction: 0.0635 is 6.35%, and 0 records a purchase that was charged
    #: no sales tax. Stamped from SALES_TAX_RATE when the item is created.
    #: Deliberately no server default: a copy of the rate baked into the schema
    #: would be a second source of truth, and a raw INSERT that omits the rate
    #: should fail loudly rather than quietly use a stale one.
    tax_rate: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), default=_configured_tax_rate, nullable=False
    )
    #: Whether shipping was part of the taxed amount. Stamped from
    #: SALES_TAX_INCLUDES_SHIPPING, for the same reason as `tax_rate`.
    tax_includes_shipping: Mapped[bool] = mapped_column(
        Boolean, default=_configured_tax_includes_shipping, nullable=False
    )
    #: Sales tax paid on the purchase. Specifically that, not "taxes" --
    #: income tax on a realised gain is a different thing entirely and this
    #: system will eventually have to speak about both.
    sales_tax: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), Computed(_TAX_EXPR, persisted=True), nullable=False
    )
    total_cost: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        Computed(f"item_cost + shipping_cost + {_TAX_EXPR}", persisted=True),
        nullable=False,
    )

    # -- valuation --------------------------------------------------------
    #: The collector premium, entered by hand. Melt value is *not* stored: it
    #: is a function of spot price, which moves daily. See the item_valuation
    #: view.
    numismatic_value: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    valuation_basis_id: Mapped[int] = mapped_column(
        ForeignKey("valuation_basis.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    composition_id: Mapped[int | None] = mapped_column(
        ForeignKey("composition.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    metal_id: Mapped[int | None] = mapped_column(
        ForeignKey("metal.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    fineness: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)

    # -- weight, in troy ounces, never a float ----------------------------
    #: Weight multiplies straight into money, so it gets the same treatment as
    #: money: NUMERIC, not float. Six decimal places represents every real
    #: figure exactly, down to a silver dime at 0.072338 ozt.
    gross_weight_ozt: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6), nullable=True
    )
    #: The melt input. A Morgan dollar weighs 0.859370 ozt but contains only
    #: 0.773440 ozt of silver, because it is 90% fine.
    fine_weight_ozt: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6), nullable=True
    )
    weight_raw: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- long tail --------------------------------------------------------
    #: Anything filtered, sorted, joined or aggregated on earns a real column.
    #: This is for the genuine long tail, not a way to avoid deciding.
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    source: Mapped[ProvenanceSource] = mapped_column(
        enum_column(ProvenanceSource, "provenance_source"),
        default=ProvenanceSource.manual,
        nullable=False,
    )

    # -- relationships ----------------------------------------------------
    purchase_order: Mapped[PurchaseOrder | None] = relationship(back_populates="items")
    item_kind: Mapped[ItemKind] = relationship()
    denomination: Mapped[Denomination | None] = relationship()
    bullion_form: Mapped[BullionForm | None] = relationship()
    set_form: Mapped[SetForm | None] = relationship()
    storage_form: Mapped[StorageForm] = relationship()
    country: Mapped[Country | None] = relationship()
    grade: Mapped[Grade | None] = relationship()
    grade_designation: Mapped[GradeDesignation | None] = relationship()
    grading_service: Mapped[GradingService | None] = relationship()
    authenticity: Mapped[Authenticity] = relationship()
    status: Mapped[ItemStatus] = relationship()
    disposition: Mapped[Disposition] = relationship()
    metal: Mapped[Metal | None] = relationship()

    #: The pieces this lot was broken into, and the lot a piece came from.
    pieces: Mapped[list[InventoryItem]] = relationship(
        back_populates="parent",
        remote_side=None,
        foreign_keys=lambda: [InventoryItem.parent_item_id],
    )
    parent: Mapped[InventoryItem | None] = relationship(
        back_populates="pieces",
        remote_side=lambda: [InventoryItem.id],
        foreign_keys=lambda: [InventoryItem.parent_item_id],
    )

    #: Photographs of this item. Ordered so the primary one comes first,
    #: which is what a thumbnail lookup wants without further sorting.
    images: Mapped[list[ItemImage]] = relationship(
        back_populates="item",
        order_by="(ItemImage.is_primary.desc(), ItemImage.sort_order)",
        cascade=_CASCADE_ALL_DELETE_ORPHAN,
    )

    #: Every offer ever made for this item. An item may be listed,
    #: withdrawn and relisted at a different price.
    listings: Mapped[list[Listing]] = relationship(back_populates="inventory_item")

    coin_detail: Mapped[CoinDetail | None] = relationship(
        back_populates="item", uselist=False, cascade=_CASCADE_ALL_DELETE_ORPHAN
    )
    currency_detail: Mapped[CurrencyDetail | None] = relationship(
        back_populates="item", uselist=False, cascade=_CASCADE_ALL_DELETE_ORPHAN
    )

    __table_args__ = (
        CheckConstraint("item_cost >= 0", name="ck_inventory_item_cost_non_negative"),
        CheckConstraint(
            "shipping_cost >= 0",
            name="ck_inventory_item_shipping_non_negative",
        ),
        CheckConstraint(
            "piece_count > 0", name="ck_inventory_item_piece_count_positive"
        ),
        CheckConstraint(
            "year_end IS NULL OR year_start IS NULL OR year_end >= year_start",
            name="ck_inventory_item_year_range",
        ),
        CheckConstraint(
            "fineness IS NULL OR (fineness > 0 AND fineness <= 1)",
            name="ck_inventory_item_fineness_fraction",
        ),
        CheckConstraint(
            "gross_weight_ozt IS NULL OR gross_weight_ozt >= 0",
            name="ck_inventory_item_gross_weight_non_negative",
        ),
        CheckConstraint(
            "fine_weight_ozt IS NULL OR fine_weight_ozt >= 0",
            name="ck_inventory_item_fine_weight_non_negative",
        ),
        CheckConstraint(
            "fine_weight_ozt IS NULL OR gross_weight_ozt IS NULL "
            "OR fine_weight_ozt <= gross_weight_ozt",
            name="ck_inventory_item_fine_within_gross",
        ),
        UniqueConstraint("item_code", name="uq_inventory_item_code"),
        # The primary browse path: "show me my coins that have arrived".
        Index("ix_inventory_item_kind_status", "item_kind_id", "status_id"),
        # Full-text search over the free-text columns. The regconfig is named
        # explicitly because to_tsvector() is only immutable -- and therefore
        # only indexable -- in its two-argument form.
        #
        # Written exactly as PostgreSQL stores it, casts and all. PostgreSQL
        # normalises an index expression on creation, so the shorter form
        # `to_tsvector('english', source_title || ' ' || description)` reflects
        # differently from how it was written and autogenerate reports the
        # index as changed on every single run -- a permanent false positive
        # in the models-versus-migrations drift test.
        Index(
            "ix_inventory_item_fts",
            text(
                "to_tsvector('english'::regconfig, "
                "(source_title::text || ' '::text) || description)"
            ),
            postgresql_using="gin",
        ),
        # Partial indexes for the search panel's facet counts.
        #
        # The plain foreign-key index on each of these already exists, but the
        # planner will not use it for a GROUP BY over the whole table -- it
        # sequential-scans and sorts. Restricting the index to live rows makes
        # it an index-only scan with the grouping already in order: measured
        # 0.91 ms -> 0.33 ms per facet, and the saving grows with the table.
        #
        # Worth the write cost here because inventory is written a few times a
        # day and searched constantly.
        Index(
            "ix_inventory_item_facet_kind",
            "item_kind_id",
            postgresql_where=text(_LIVE_ROWS_ONLY),
        ),
        Index(
            "ix_inventory_item_facet_grade",
            "grade_id",
            postgresql_where=text(_LIVE_ROWS_ONLY),
        ),
        Index(
            "ix_inventory_item_facet_metal",
            "metal_id",
            postgresql_where=text(_LIVE_ROWS_ONLY),
        ),
        Index(
            "ix_inventory_item_facet_country",
            "country_id",
            postgresql_where=text(_LIVE_ROWS_ONLY),
        ),
        Index(
            "ix_inventory_item_facet_status",
            "status_id",
            postgresql_where=text(_LIVE_ROWS_ONLY),
        ),
        Index(
            "ix_inventory_item_facet_disposition",
            "disposition_id",
            postgresql_where=text(_LIVE_ROWS_ONLY),
        ),
        Index(
            "ix_inventory_item_facet_bullion",
            "bullion_form_id",
            postgresql_where=text(_LIVE_ROWS_ONLY),
        ),
        Index(
            "ix_inventory_item_attributes",
            "attributes",
            postgresql_using="gin",
        ),
    )


class CoinDetail(Base):
    """Coin-only attributes.

    1:1 with an item, every column nullable -- an item identified only by a
    photograph is still a valid row.
    """

    __tablename__ = "coin_detail"

    inventory_item_id: Mapped[int] = mapped_column(
        ForeignKey(_FK_INVENTORY_ITEM, ondelete="CASCADE"), primary_key=True
    )
    #: A mint mark. Structurally identical to a banknote's series letter and
    #: semantically unrelated to it -- hence separate columns on separate
    #: tables. `1921-D` is a mint; `1957-B` is a series.
    mint_id: Mapped[int | None] = mapped_column(
        ForeignKey("mint.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    variety: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pcgs_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("pcgs_type.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    pcgs_status: Mapped[str] = mapped_column(
        String(16), default="unknown", server_default=text("'unknown'"), nullable=False
    )

    item: Mapped[InventoryItem] = relationship(back_populates="coin_detail")

    __table_args__ = (
        CheckConstraint(
            "pcgs_status IN ('unknown', 'proposed', 'confirmed', 'conflicting')",
            name="ck_coin_detail_pcgs_status",
        ),
    )


class CurrencyDetail(Base):
    """Banknote-only attributes. 1:1 with an item, every column nullable."""

    __tablename__ = "currency_detail"

    inventory_item_id: Mapped[int] = mapped_column(
        ForeignKey(_FK_INVENTORY_ITEM, ondelete="CASCADE"), primary_key=True
    )
    note_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("note_type.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    series_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: A series letter, not a mint mark. See the note on CoinDetail.mint_id.
    series_letter: Mapped[str | None] = mapped_column(String(4), nullable=True)
    #: How a collector writes the series: 1935A, 1957B, or plain 1935.
    #:
    #: Generated rather than stored, because the year and the letter are what
    #: filtering and sorting need and this is what reading needs. Two stored
    #: copies of one fact could disagree; a generated column cannot.
    series_designation: Mapped[str | None] = mapped_column(
        String(16),
        Computed(
            "CASE WHEN series_year IS NULL THEN NULL "
            "ELSE series_year::text || coalesce(series_letter, '') END",
            persisted=True,
        ),
        nullable=True,
    )
    seal_color_id: Mapped[int | None] = mapped_column(
        ForeignKey("seal_color.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    signature_combination_id: Mapped[int | None] = mapped_column(
        ForeignKey("signature_combination.id", ondelete="RESTRICT"),
        index=True,
        nullable=True,
    )
    fed_district_id: Mapped[int | None] = mapped_column(
        ForeignKey("fed_district.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    #: Printed on the note. Text, always: leading zeros and star suffixes are
    #: meaning, not formatting. Distinct from a grading certificate serial,
    #: which identifies a holder rather than a note.
    serial_number: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    friedberg_id: Mapped[int | None] = mapped_column(
        ForeignKey("friedberg_number.id", ondelete="RESTRICT"),
        index=True,
        nullable=True,
    )
    friedberg_status: Mapped[str] = mapped_column(
        String(16), default="unknown", server_default=text("'unknown'"), nullable=False
    )

    # -- plate markings -----------------------------------------------------
    # A currency mule is a note whose face and back plates come from
    # different design eras -- mismatched, rather than absent. Without these
    # recorded, a mule cannot be identified at all.
    #
    # All three are text, not integers, for the same reason as
    # `purchase_order.order_number`: storing them as a number destroys
    # information irreversibly. A face plate designation carries a check
    # letter (`E82`), a plate position is a check letter plus a quadrant
    # number, and formats vary by era.
    #: The plate that printed the face, e.g. `E82`.
    face_plate_number: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: The plate that printed the back, e.g. `E82`.
    back_plate_number: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: Where on the printing sheet this note sat: a check letter plus a
    #: quadrant number.
    plate_position: Mapped[str | None] = mapped_column(String(16), nullable=True)

    item: Mapped[InventoryItem] = relationship(back_populates="currency_detail")

    __table_args__ = (
        CheckConstraint(
            "friedberg_status IN ('unknown', 'proposed', 'confirmed', 'conflicting')",
            name="ck_currency_detail_friedberg_status",
        ),
    )
