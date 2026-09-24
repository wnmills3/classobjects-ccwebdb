"""UCAM is its own designation, not another name for DCAM.

The seed made NGC's Ultra Cameo an alias of PCGS's Deep Cameo, so every
"UCAM" or "Ultra Cameo" read from a rating was stored as DCAM. The owner's
ruling (2026-09-24): a row each, since the holder says one or the other.
Seed loads only ever add aliases, so the three that pointed at DCAM --
"UCAM", "Ultra Cameo", "UC" -- are removed here; the seed then adds the
UCAM row and gives it "Ultra Cameo" and "UC". Items already stored as DCAM
are re-designated separately, from each item's own wording.

Revision ID: e2b8c4d6f1a3
Revises: d7e1f3a9c2b4
Create Date: 2026-09-24 13:45:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2b8c4d6f1a3"
down_revision: str | None = "d7e1f3a9c2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ALIASES = ("UCAM", "Ultra Cameo", "UC")


def upgrade() -> None:
    """Detach UCAM, Ultra Cameo and UC from DCAM."""
    op.execute(
        sa.text(
            "DELETE FROM reference_alias WHERE table_name = 'grade_designation' "
            "AND alias IN :aliases AND row_id = "
            "(SELECT id FROM grade_designation WHERE code = 'DCAM')"
        ).bindparams(sa.bindparam("aliases", _ALIASES, expanding=True))
    )


def downgrade() -> None:
    """Point them at DCAM again, taking them from UCAM if the seed moved them."""
    op.execute(
        sa.text(
            "DELETE FROM reference_alias WHERE table_name = 'grade_designation' "
            "AND alias IN :aliases"
        ).bindparams(sa.bindparam("aliases", _ALIASES, expanding=True))
    )
    for alias in _ALIASES:
        op.execute(
            sa.text(
                "INSERT INTO reference_alias (table_name, row_id, alias) "
                "SELECT 'grade_designation', id, :alias FROM grade_designation "
                "WHERE code = 'DCAM'"
            ).bindparams(alias=alias)
        )
