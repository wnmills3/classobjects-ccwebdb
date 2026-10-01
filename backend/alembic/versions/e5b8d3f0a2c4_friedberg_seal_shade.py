"""Friedberg numbers: read the star and the mule past a seal shade.

A light or dark green seal is written after the number -- `2008-B LGS`,
`2008-B* LGS` (owner, 2026-10-01) -- so `is_star` and `is_mule`, which read
the end of `fr_number`, would call `2008-B* LGS` neither and refuse it beside
`2008-B LGS`. Both expressions now look past the shade, as
`app.fr_format.fr_traits` does.

`SET EXPRESSION` (PostgreSQL 17+) recomputes every row and rebuilds the
indexes that read the columns. No number on file carries a shade yet, so no
value changes.

Revision ID: e5b8d3f0a2c4
Revises: d4a7c2e9f1b3
Create Date: 2026-10-01 12:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e5b8d3f0a2c4"
down_revision: str | None = "d4a7c2e9f1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _set(column: str, expression: str) -> None:
    op.execute(
        f"ALTER TABLE friedberg_number ALTER COLUMN {column} "
        f"SET EXPRESSION AS ({expression})"
    )


def upgrade() -> None:
    """Read both traits past a trailing ` LGS` or ` DGS`."""
    _set("is_star", "fr_number ~ '[*]( (LGS|DGS))?$'")
    _set("is_mule", "fr_number ~ 'm[*]?( (LGS|DGS))?$'")


def downgrade() -> None:
    """Read both traits from the very end of the number again."""
    _set("is_star", "fr_number LIKE '%*'")
    _set("is_mule", "fr_number ~ 'm[*]?$'")
