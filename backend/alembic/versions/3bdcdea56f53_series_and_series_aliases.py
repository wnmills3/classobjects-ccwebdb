"""series and series aliases

A design series -- Morgan Dollar, Winged Liberty Head Dime -- is the industry's
own unit of organisation, so using it is what lets a value be looked up against
a published price guide.

Aliases are a separate table rather than extra columns because the relationship
is many-to-one in both directions: a series has several nicknames, and one
nickname spans several series. They matter more than they sound. In this
collection's own descriptions "Winged Liberty Head" appears zero times and
"Mercury" appears 104, so a vocabulary holding only formal names would find
nothing.

`series_id` sits on `inventory_item` rather than on `coin_detail` so faceting
groups by an indexed foreign key on the table already being scanned -- grouping
by a joined column was previously measured as most of the cost of a search.

Revision ID: 3bdcdea56f53
Revises: 449fdc4a350d
Create Date: 2026-09-07 21:16:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "3bdcdea56f53"
down_revision: str | None = "449fdc4a350d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The type already exists -- every other classifier table uses it. Declaring
#: it with create_type=False references it instead of trying to CREATE TYPE a
#: second time, which fails.
_PROVENANCE = postgresql.ENUM(
    "seeded", "derived", "manual", name="provenance_source", create_type=False
)


def upgrade() -> None:
    op.create_table(
        "series",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("source", _PROVENANCE, nullable=False),
        sa.Column("year_start", sa.Integer(), nullable=True),
        sa.Column("year_end", sa.Integer(), nullable=True),
        sa.Column(
            "applies_to",
            sa.String(length=16),
            server_default=sa.text("'coin'"),
            nullable=False,
        ),
        sa.Column("denomination_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["denomination_id"],
            ["denomination.id"],
            name="fk_series_denomination_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_series_code"),
    )
    op.create_table(
        "series_alias",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("series_id", sa.Integer(), nullable=False),
        sa.Column("alias", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["series_id"],
            ["series.id"],
            name="fk_series_alias_series_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("series_id", "alias", name="uq_series_alias"),
    )
    op.create_index(
        op.f("ix_series_alias_series_id"), "series_alias", ["series_id"], unique=False
    )

    op.add_column("inventory_item", sa.Column("series_id", sa.Integer(), nullable=True))
    op.create_index(
        op.f("ix_inventory_item_series_id"),
        "inventory_item",
        ["series_id"],
        unique=False,
    )
    # Named, because autogenerate emits None here and the matching
    # drop_constraint(None, ...) in downgrade then fails outright with
    # "cannot emit DROP CONSTRAINT ... it has no name".
    op.create_foreign_key(
        "fk_inventory_item_series_id",
        "inventory_item",
        "series",
        ["series_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_inventory_item_series_id", "inventory_item", type_="foreignkey"
    )
    op.drop_index(op.f("ix_inventory_item_series_id"), table_name="inventory_item")
    op.drop_column("inventory_item", "series_id")
    op.drop_index(op.f("ix_series_alias_series_id"), table_name="series_alias")
    op.drop_table("series_alias")
    op.drop_table("series")
    # provenance_source is deliberately not dropped: it is shared with every
    # other classifier table and did not originate here.
