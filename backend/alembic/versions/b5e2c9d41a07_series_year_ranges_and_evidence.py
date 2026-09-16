"""series year ranges, and evidence for note designs

A design can need several runs of years -- the Morgan dollar is 1878-1904,
1921 and 2021 on -- and some pair different denominations with different
series, so ranges live in their own table with their own denomination and
allowed series letters. Designs that share their series with ordinary notes
(Hawaii, North Africa) are assigned only on evidence, so a design records
whether it needs any and which seal colour counts. `yellow` joins the seal
colours for North Africa; it is seeded, not added here.

No data moves: existing designs have no ranges, and a design without any is
matched on its own span, exactly as before.

Revision ID: b5e2c9d41a07
Revises: a3d9f1c27b40
Create Date: 2026-09-16 11:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b5e2c9d41a07"
down_revision: str | None = "a3d9f1c27b40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the range table and the two evidence columns on `series`."""
    op.add_column(
        "series",
        sa.Column(
            "needs_evidence",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column("series", sa.Column("seal_color_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_series_seal_color_id",
        "series",
        "seal_color",
        ["seal_color_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "series_year_range",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("series_id", sa.Integer(), nullable=False),
        sa.Column("denomination_id", sa.Integer(), nullable=True),
        sa.Column("year_start", sa.Integer(), nullable=False),
        sa.Column("year_end", sa.Integer(), nullable=True),
        sa.Column("letters", sa.String(length=27), nullable=True),
        sa.ForeignKeyConstraint(
            ["series_id"],
            ["series.id"],
            name="fk_series_year_range_series_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["denomination_id"],
            ["denomination.id"],
            name="fk_series_year_range_denomination_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "series_id",
            "denomination_id",
            "year_start",
            name="uq_series_year_range",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        "ix_series_year_range_series_id", "series_year_range", ["series_id"]
    )


def downgrade() -> None:
    """Remove the range table and the evidence columns."""
    op.drop_index("ix_series_year_range_series_id", table_name="series_year_range")
    op.drop_table("series_year_range")
    op.drop_constraint(
        "fk_series_seal_color_id", "series", type_="foreignkey"
    )
    op.drop_column("series", "seal_color_id")
    op.drop_column("series", "needs_evidence")
