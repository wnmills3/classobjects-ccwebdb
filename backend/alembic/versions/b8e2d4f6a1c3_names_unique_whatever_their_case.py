"""Vendors, sellers and storage locations: unique whatever their case.

The API already refuses "eBay" beside "ebay" -- but with a SELECT, and the
constraints behind it compared names exactly. Two people adding "eBay" and
"ebay" at the same moment each passed the check, and the database took
both, splitting one source's purchases across two rows.

Each identity is now a unique index on the lowered text. A storage
location's is its kind, institution and box, each absent part read as
empty: one index for what took two (Postgres treats two NULLs as distinct,
so the box-less case needed its own partial index).

Live data held no two rows these indexes refuse when this was written.

Revision ID: b8e2d4f6a1c3
Revises: c9f5b1e3a7d4
Create Date: 2026-10-14 10:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8e2d4f6a1c3"
down_revision: str | None = "c9f5b1e3a7d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Replace the exact-match constraints with indexes on the lowered text."""
    op.drop_constraint("uq_vendor_name", "vendor", type_="unique")
    op.create_index(
        "uq_vendor_name", "vendor", [sa.text("lower(name::text)")], unique=True
    )
    op.drop_constraint("uq_seller_name", "seller", type_="unique")
    op.create_index(
        "uq_seller_name", "seller", [sa.text("lower(name::text)")], unique=True
    )
    op.drop_index(
        "uq_storage_location_identity_no_identifier", table_name="storage_location"
    )
    op.drop_constraint(
        "uq_storage_location_identity", "storage_location", type_="unique"
    )
    op.create_index(
        "uq_storage_location_identity",
        "storage_location",
        [
            "storage_location_kind_id",
            sa.text("lower(COALESCE(institution, ''::character varying)::text)"),
            sa.text("lower(COALESCE(identifier, ''::character varying)::text)"),
        ],
        unique=True,
    )


def downgrade() -> None:
    """Back to exact-match constraints, and the box-less partial index."""
    op.drop_index("uq_storage_location_identity", table_name="storage_location")
    op.create_unique_constraint(
        "uq_storage_location_identity",
        "storage_location",
        ["storage_location_kind_id", "institution", "identifier"],
    )
    op.create_index(
        "uq_storage_location_identity_no_identifier",
        "storage_location",
        ["storage_location_kind_id", "institution"],
        unique=True,
        postgresql_where=sa.text("identifier IS NULL"),
    )
    op.drop_index("uq_seller_name", table_name="seller")
    op.create_unique_constraint("uq_seller_name", "seller", ["name"])
    op.drop_index("uq_vendor_name", table_name="vendor")
    op.create_unique_constraint("uq_vendor_name", "vendor", ["name"])
