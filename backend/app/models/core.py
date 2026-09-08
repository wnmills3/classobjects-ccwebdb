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
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import (
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

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
    ErrorType,
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

#: The default sales-tax rate applied to acquisitions. Stored per row rather
#: than baked into the generated expression, because rates vary by
#: jurisdiction and change over time.
DEFAULT_TAX_RATE = Decimal("0.0635")

#: `taxes` and `total_cost` are both generated. PostgreSQL forbids a generated
#: column from referencing another generated column, so the tax expression is
#: repeated inside the total rather than referenced -- keeping them as one
#: constant here means the two can never drift apart.
_TAX_EXPR = "round((price + shipping) * tax_rate, 2)"

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

    __mapper_args__: ClassVar[dict[str, object]] = {"version_id_col": version}

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
    #: Pieces in the lot. A multi-quantity row stays one row: it was bought as
    #: a lot and is stored as a lot.
    storage_quantity: Mapped[int] = mapped_column(
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

    # -- errors -----------------------------------------------------------
    #: Never inferred from free text: description fields are seller prose, and
    #: keyword matching against them is unreliable in both directions.
    error_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("error_type.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    error_details: Mapped[str | None] = mapped_column(Text, nullable=True)

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
        ForeignKey("inventory_item.id", ondelete="RESTRICT"),
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

    # -- description ------------------------------------------------------
    #: A number the owner assigned by their own scheme, before this system
    #: existed. Distinct from `item_code`: not unique, not issued here, and
    #: kept only so their old references still resolve.
    local_catalog_number: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(
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
    price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00"), nullable=False
    )
    shipping: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00"), nullable=False
    )
    tax_rate: Mapped[Decimal] = mapped_column(
        Numeric(6, 4),
        default=DEFAULT_TAX_RATE,
        server_default=text("0.0635"),
        nullable=False,
    )
    taxes: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), Computed(_TAX_EXPR, persisted=True), nullable=False
    )
    total_cost: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        Computed(f"price + shipping + {_TAX_EXPR}", persisted=True),
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
    error_type: Mapped[ErrorType | None] = relationship()
    status: Mapped[ItemStatus] = relationship()
    disposition: Mapped[Disposition] = relationship()
    metal: Mapped[Metal | None] = relationship()

    #: The pieces this lot was broken into, and the lot a piece came from.
    pieces: Mapped[list[InventoryItem]] = relationship(
        back_populates="parent",
        remote_side=lambda: None,
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
        cascade="all, delete-orphan",
    )

    #: Every offer ever made for this item. An item may be listed,
    #: withdrawn and relisted at a different price.
    listings: Mapped[list[Listing]] = relationship(back_populates="inventory_item")

    coin_detail: Mapped[CoinDetail | None] = relationship(
        back_populates="item", uselist=False, cascade="all, delete-orphan"
    )
    currency_detail: Mapped[CurrencyDetail | None] = relationship(
        back_populates="item", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_inventory_item_price_non_negative"),
        CheckConstraint(
            "shipping >= 0", name="ck_inventory_item_shipping_non_negative"
        ),
        CheckConstraint(
            "storage_quantity > 0", name="ck_inventory_item_quantity_positive"
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
        # `to_tsvector('english', title || ' ' || description)` reflects back
        # differently from how it was written and autogenerate reports the
        # index as changed on every single run -- a permanent false positive
        # in the models-versus-migrations drift test.
        Index(
            "ix_inventory_item_fts",
            text(
                "to_tsvector('english'::regconfig, "
                "(title::text || ' '::text) || description)"
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
            postgresql_where=text("split_at IS NULL"),
        ),
        Index(
            "ix_inventory_item_facet_grade",
            "grade_id",
            postgresql_where=text("split_at IS NULL"),
        ),
        Index(
            "ix_inventory_item_facet_metal",
            "metal_id",
            postgresql_where=text("split_at IS NULL"),
        ),
        Index(
            "ix_inventory_item_facet_country",
            "country_id",
            postgresql_where=text("split_at IS NULL"),
        ),
        Index(
            "ix_inventory_item_facet_status",
            "status_id",
            postgresql_where=text("split_at IS NULL"),
        ),
        Index(
            "ix_inventory_item_facet_disposition",
            "disposition_id",
            postgresql_where=text("split_at IS NULL"),
        ),
        Index(
            "ix_inventory_item_facet_bullion",
            "bullion_form_id",
            postgresql_where=text("split_at IS NULL"),
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
        ForeignKey("inventory_item.id", ondelete="CASCADE"), primary_key=True
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
        ForeignKey("inventory_item.id", ondelete="CASCADE"), primary_key=True
    )
    note_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("note_type.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    series_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: A series letter, not a mint mark. See the note on CoinDetail.mint_id.
    series_letter: Mapped[str | None] = mapped_column(String(4), nullable=True)
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

    item: Mapped[InventoryItem] = relationship(back_populates="currency_detail")

    __table_args__ = (
        CheckConstraint(
            "friedberg_status IN ('unknown', 'proposed', 'confirmed', 'conflicting')",
            name="ck_currency_detail_friedberg_status",
        ),
    )
