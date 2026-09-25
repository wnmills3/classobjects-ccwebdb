"""Where a note was printed: Washington (dc) or Fort Worth (fw).

The owner, 2026-09-25: the printing location, with the face and back plate
numbers already on `currency_detail`, tells two Friedberg numbers apart -- a
2017-A $1 is 3005-A from Washington, 3006-A from Fort Worth -- so it is on the
note and on the catalogue row, and part of the catalogue's identity index as
`web_press` is. Every value starts empty; a note's is read from its face plate
when one is recorded (`app.plates`).

Revision ID: 6e0a3c8d1b57
Revises: 2d7f4b9e6a13
Create Date: 2026-09-25 00:40:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6e0a3c8d1b57"
down_revision: str | None = "2d7f4b9e6a13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IDENTITY = (
    "CREATE UNIQUE INDEX uq_friedberg_number_identity ON friedberg_number "
    "USING btree (denomination_id, series_year, series_letter, note_type_id, "
    "district_letter, web_press, signature_combination_id, seal_color_id{extra}) "
    "NULLS NOT DISTINCT WHERE ((denomination_id IS NOT NULL) AND "
    "(series_year IS NOT NULL) AND (note_type_id IS NOT NULL))"
)


def upgrade() -> None:
    """Add the column to the note and the catalogue, and to the identity."""
    for table in ("currency_detail", "friedberg_number"):
        op.add_column(table, sa.Column("printing_facility", sa.String(2), nullable=True))
        op.create_check_constraint(
            f"ck_{table}_printing_facility",
            table,
            "printing_facility IS NULL OR printing_facility IN ('dc', 'fw')",
        )
    op.execute("DROP INDEX uq_friedberg_number_identity")
    op.execute(_IDENTITY.format(extra=", printing_facility"))


def downgrade() -> None:
    """The identity without it, then the columns go."""
    op.execute("DROP INDEX uq_friedberg_number_identity")
    op.execute(_IDENTITY.format(extra=""))
    for table in ("friedberg_number", "currency_detail"):
        op.drop_constraint(f"ck_{table}_printing_facility", table, type_="check")
        op.drop_column(table, "printing_facility")
