"""Sales platforms; every listing and order names one.

Adds the sales_venue_kind vocabulary and the sales_venue table, creates the
web store platform, and points every existing listing and sales order at it.
Listings gain a format, a status and the platform's own ids; `is_active`
becomes a column generated from `status`, so its readers are unchanged. The
public catalogue shows only the store's fixed-price listings.

Revision ID: d6a1f3b8c402
Revises: c2d7a9e5f614
Create Date: 2026-09-17 13:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.models.views import DROP_VIEWS, create_views

revision: str = "d6a1f3b8c402"
down_revision: str | None = "c2d7a9e5f614"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCE = postgresql.ENUM(
    "seeded", "derived", "manual", name="provenance_source", create_type=False
)
_FORMAT = postgresql.ENUM("fixed_price", "auction", name="listing_format")
_STATUS = postgresql.ENUM("active", "paused", "ended", name="listing_status")

#: As in operations.json; the seed loader later upserts the same codes.
_KINDS = (
    ("own_store", "Our web store", 10),
    ("marketplace", "Marketplace", 20),
    ("live_auction", "Live auction show", 30),
    ("auction_house", "Auction house (agent)", 40),
)


def upgrade() -> None:
    """Create platforms, the store, and the listing and order columns."""
    bind = op.get_bind()
    _FORMAT.create(bind)
    _STATUS.create(bind)

    op.create_table(
        "sales_venue_kind",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("source", _SOURCE, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_sales_venue_kind_code"),
    )
    for code, label, order in _KINDS:
        bind.execute(
            sa.text(
                "INSERT INTO sales_venue_kind "
                "(code, label, sort_order, is_active, source) "
                "VALUES (:c, :l, :o, true, 'seeded')"
            ),
            {"c": code, "l": label, "o": order},
        )

    op.create_table(
        "sales_venue",
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("sales_venue_kind_id", sa.Integer(), nullable=False),
        sa.Column(
            "is_own_store", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("vendor_id", sa.Integer(), nullable=True),
        sa.Column("account_handle", sa.String(length=255), nullable=True),
        sa.Column("listing_url_template", sa.String(length=500), nullable=True),
        sa.Column("commission_rate", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column("processing_rate", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column("processing_fixed", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("listing_fee", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("terms_as_of", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "commission_rate >= 0 AND commission_rate <= 1",
            name="ck_sales_venue_commission_rate",
        ),
        sa.CheckConstraint(
            "processing_rate >= 0 AND processing_rate <= 1",
            name="ck_sales_venue_processing_rate",
        ),
        sa.CheckConstraint(
            "processing_fixed >= 0", name="ck_sales_venue_processing_fixed"
        ),
        sa.CheckConstraint("listing_fee >= 0", name="ck_sales_venue_listing_fee"),
        sa.ForeignKeyConstraint(
            ["sales_venue_kind_id"],
            ["sales_venue_kind.id"],
            name="fk_sales_venue_sales_venue_kind_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["vendor_id"],
            ["vendor.id"],
            name="fk_sales_venue_vendor_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_sales_venue_code"),
        sa.UniqueConstraint("vendor_id", name="uq_sales_venue_vendor"),
    )
    op.create_index(
        "ix_sales_venue_sales_venue_kind_id", "sales_venue", ["sales_venue_kind_id"]
    )
    op.create_index(
        "uq_sales_venue_own_store",
        "sales_venue",
        ["is_own_store"],
        unique=True,
        postgresql_where=sa.text("is_own_store"),
    )
    store_id = bind.execute(
        sa.text(
            "INSERT INTO sales_venue "
            "(code, name, sales_venue_kind_id, is_own_store, created_at, updated_at) "
            "SELECT 'store', 'Web store', id, true, now(), now() "
            "FROM sales_venue_kind WHERE code = 'own_store' RETURNING id"
        )
    ).scalar_one()

    for statement in DROP_VIEWS:
        op.execute(statement)

    # listing: platform, format, status, external ids
    op.add_column("listing", sa.Column("sales_venue_id", sa.Integer(), nullable=True))
    bind.execute(sa.text("UPDATE listing SET sales_venue_id = :s"), {"s": store_id})
    op.alter_column("listing", "sales_venue_id", nullable=False)
    op.create_foreign_key(
        "fk_listing_sales_venue_id",
        "listing",
        "sales_venue",
        ["sales_venue_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_listing_sales_venue_id", "listing", ["sales_venue_id"])

    op.add_column(
        "listing",
        sa.Column(
            "format",
            postgresql.ENUM(name="listing_format", create_type=False),
            server_default="fixed_price",
            nullable=False,
        ),
    )
    op.add_column(
        "listing",
        sa.Column(
            "status",
            postgresql.ENUM(name="listing_status", create_type=False),
            server_default="active",
            nullable=False,
        ),
    )
    op.execute("UPDATE listing SET status = 'ended' WHERE NOT is_active")
    # The defaults served only the backfill; the application always sets both.
    op.alter_column("listing", "format", server_default=None)
    op.alter_column("listing", "status", server_default=None)

    op.drop_index("ix_listing_active", table_name="listing")
    op.drop_column("listing", "is_active")
    op.add_column(
        "listing",
        sa.Column(
            "is_active",
            sa.Boolean(),
            sa.Computed("status = 'active'::listing_status", persisted=True),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_listing_active",
        "listing",
        ["inventory_item_id"],
        postgresql_where=sa.text("is_active"),
    )
    op.add_column("listing", sa.Column("external_id", sa.String(length=128), nullable=True))
    op.add_column(
        "listing", sa.Column("external_url", sa.String(length=1000), nullable=True)
    )

    # sales_order: platform and the platform's order number
    op.add_column(
        "sales_order", sa.Column("sales_venue_id", sa.Integer(), nullable=True)
    )
    bind.execute(sa.text("UPDATE sales_order SET sales_venue_id = :s"), {"s": store_id})
    op.alter_column("sales_order", "sales_venue_id", nullable=False)
    op.create_foreign_key(
        "fk_sales_order_sales_venue_id",
        "sales_order",
        "sales_venue",
        ["sales_venue_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_sales_order_sales_venue_id", "sales_order", ["sales_venue_id"])
    op.add_column(
        "sales_order",
        sa.Column("external_order_id", sa.String(length=128), nullable=True),
    )

    for statement in create_views():
        op.execute(statement)


def downgrade() -> None:
    """Drop platforms; listings keep only whether they were active."""
    for statement in DROP_VIEWS:
        op.execute(statement)

    op.drop_column("sales_order", "external_order_id")
    op.drop_index("ix_sales_order_sales_venue_id", table_name="sales_order")
    op.drop_constraint(
        "fk_sales_order_sales_venue_id", "sales_order", type_="foreignkey"
    )
    op.drop_column("sales_order", "sales_venue_id")

    op.drop_column("listing", "external_url")
    op.drop_column("listing", "external_id")
    op.drop_index("ix_listing_active", table_name="listing")
    op.add_column(
        "listing",
        sa.Column(
            "is_active_plain",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.execute("UPDATE listing SET is_active_plain = is_active")
    op.drop_column("listing", "is_active")
    op.alter_column("listing", "is_active_plain", new_column_name="is_active")
    op.create_index(
        "ix_listing_active",
        "listing",
        ["inventory_item_id"],
        postgresql_where=sa.text("is_active"),
    )
    op.drop_column("listing", "status")
    op.drop_column("listing", "format")
    op.drop_index("ix_listing_sales_venue_id", table_name="listing")
    op.drop_constraint("fk_listing_sales_venue_id", "listing", type_="foreignkey")
    op.drop_column("listing", "sales_venue_id")

    op.drop_index("uq_sales_venue_own_store", table_name="sales_venue")
    op.drop_index("ix_sales_venue_sales_venue_kind_id", table_name="sales_venue")
    op.drop_table("sales_venue")
    op.drop_table("sales_venue_kind")
    op.execute("DROP TYPE IF EXISTS listing_status")
    op.execute("DROP TYPE IF EXISTS listing_format")

    for statement in create_views(selling=False):
        op.execute(statement)
