"""Offer claims: one active offer per item, and the pause link on listing.

Adds `offer_claim`, the row a claim -- active, paused or released -- holds
against one listing and one item. Its partial unique index on
`inventory_item_id` where `state = 'active'` is the database guarantee that
an item is never offered in two places at once; that guarantee has to live
on a per-item row rather than on `listing` because a lot listing (phase 3)
will offer several items at once. `listing.paused_by_listing_id` records
which offer paused a store listing, so settling that offer knows which
listings to resume.

Every existing listing is backfilled a claim -- `active` for an active
listing, `paused` for a paused one, `released` otherwise -- so the
one-active-offer invariant holds from the first moment this migration runs,
not only for rows written afterward.

Revision ID: e7c3a5b19d84
Revises: d6a1f3b8c402
Create Date: 2026-09-17 14:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e7c3a5b19d84"
down_revision: str | None = "d6a1f3b8c402"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATE = postgresql.ENUM("active", "paused", "released", name="offer_claim_state")


def upgrade() -> None:
    """Create `offer_claim`, the pause link, and backfill every listing."""
    bind = op.get_bind()
    _STATE.create(bind)

    op.create_table(
        "offer_claim",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("inventory_item_id", sa.Integer(), nullable=False),
        sa.Column("listing_id", sa.Integer(), nullable=False),
        sa.Column(
            "state",
            postgresql.ENUM(name="offer_claim_state", create_type=False),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            ["inventory_item.id"],
            name="fk_offer_claim_inventory_item_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listing.id"],
            name="fk_offer_claim_listing_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "listing_id", "inventory_item_id", name="uq_offer_claim_pair"
        ),
    )
    op.create_index(
        "ix_offer_claim_inventory_item_id", "offer_claim", ["inventory_item_id"]
    )
    op.create_index("ix_offer_claim_listing_id", "offer_claim", ["listing_id"])
    op.create_index(
        "uq_offer_claim_active",
        "offer_claim",
        ["inventory_item_id"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
    )

    op.add_column(
        "listing", sa.Column("paused_by_listing_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_listing_paused_by_listing_id",
        "listing",
        "listing",
        ["paused_by_listing_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # Every existing listing already claims its item; a claim written from
    # here on is `offering_writes`'s job, but the invariant must hold for
    # rows that predate it.
    bind.execute(
        sa.text(
            "INSERT INTO offer_claim "
            "(inventory_item_id, listing_id, state, created_at, updated_at) "
            "SELECT inventory_item_id, id, "
            "CASE status "
            "  WHEN 'active' THEN 'active'::offer_claim_state "
            "  WHEN 'paused' THEN 'paused'::offer_claim_state "
            "  ELSE 'released'::offer_claim_state END, "
            "now(), now() FROM listing WHERE inventory_item_id IS NOT NULL"
        )
    )


def downgrade() -> None:
    """Drop the pause link and `offer_claim`."""
    op.drop_constraint(
        "fk_listing_paused_by_listing_id", "listing", type_="foreignkey"
    )
    op.drop_column("listing", "paused_by_listing_id")

    op.drop_index("uq_offer_claim_active", table_name="offer_claim")
    op.drop_index("ix_offer_claim_listing_id", table_name="offer_claim")
    op.drop_index("ix_offer_claim_inventory_item_id", table_name="offer_claim")
    op.drop_table("offer_claim")
    op.execute("DROP TYPE IF EXISTS offer_claim_state")
