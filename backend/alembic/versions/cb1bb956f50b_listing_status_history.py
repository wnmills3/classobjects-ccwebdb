"""Listing status history, and two indexes the auctions phase deferred.

- `listing_status_history`: every status a listing has had, written by
  `offering_writes` in the same flush as the change -- the offer timeline
  across re-offers, and whether an ending was a sale or a withdrawal, which
  auction settlement made part of the financial record. Existing listings
  are backfilled from `listed_at`, `ended_at` and `status`, each backfilled
  row saying so in its note.
- `ix_auction_lot_buyer_customer_id`: the one foreign key on `auction_lot`
  with no index, deferred from the auctions phase.
- `uq_storage_location_identity_no_identifier`: `uq_storage_location_identity`
  never fires for a location with no box number, because Postgres treats two
  NULLs as distinct; two raced first consignments to one house could each
  create a "Consigned" location. This partial index is what the constraint
  meant for that case. A database that already holds such duplicates fails
  here, loudly -- merge them first; live held none on 2026-09-22.

Revision ID: cb1bb956f50b
Revises: e267ec3aedc1
Create Date: 2026-09-22 21:40:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "cb1bb956f50b"
down_revision: str | None = "e267ec3aedc1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the history table, backfill it, and add the two indexes."""
    op.create_table(
        "listing_status_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("listing_id", sa.Integer(), nullable=False),
        sa.Column(
            "from_status",
            postgresql.ENUM(name="listing_status", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "to_status",
            postgresql.ENUM(name="listing_status", create_type=False),
            nullable=False,
        ),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listing.id"],
            name="fk_listing_status_history_listing_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_listing_status_history_listing_time",
        "listing_status_history",
        ["listing_id", "changed_at"],
    )
    # The opening row for every existing listing, then one more for any that
    # is no longer active. When a paused listing was paused is not recorded
    # anywhere, so its row takes `updated_at` and says it is an estimate.
    op.execute(
        "INSERT INTO listing_status_history "
        "(listing_id, from_status, to_status, changed_at, note) "
        "SELECT id, NULL, 'active', listed_at, 'offered (backfilled)' "
        "FROM listing"
    )
    op.execute(
        "INSERT INTO listing_status_history "
        "(listing_id, from_status, to_status, changed_at, note) "
        "SELECT id, 'active', status, "
        "CASE WHEN status = 'ended' THEN coalesce(ended_at, updated_at) "
        "ELSE updated_at END, "
        "CASE WHEN status = 'ended' THEN 'ended (backfilled)' "
        "ELSE 'paused (backfilled; time estimated)' END "
        "FROM listing WHERE status <> 'active'"
    )

    op.create_index(
        "ix_auction_lot_buyer_customer_id", "auction_lot", ["buyer_customer_id"]
    )
    op.create_index(
        "uq_storage_location_identity_no_identifier",
        "storage_location",
        ["storage_location_kind_id", "institution"],
        unique=True,
        postgresql_where=sa.text("identifier IS NULL"),
    )


def downgrade() -> None:
    """Drop the indexes, then the history table. The enum type stays: `listing` owns it."""
    op.drop_index(
        "uq_storage_location_identity_no_identifier", table_name="storage_location"
    )
    op.drop_index("ix_auction_lot_buyer_customer_id", table_name="auction_lot")
    op.drop_index(
        "ix_listing_status_history_listing_time", table_name="listing_status_history"
    )
    op.drop_table("listing_status_history")
