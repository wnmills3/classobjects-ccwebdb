"""Item codes keep every digit past CC-999999.

The default padded the sequence value to six characters with `lpad`, which
also *cuts* to six: the millionth item would have been given `CC-100000`, a
code already held. `item_code_format()` pads to at least six digits and keeps
the rest; the default calls it on one `nextval`. Its text is the model's
`ITEM_CODE_FORMAT_SQL`, written out here so the revision stays as it was.

Revision ID: b4d6f8a0c2e4
Revises: a3c5e7f9b1d2
Create Date: 2026-10-21 12:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b4d6f8a0c2e4"
down_revision: str | None = "a3c5e7f9b1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FUNCTION = (
    "CREATE OR REPLACE FUNCTION item_code_format(n bigint) RETURNS text\n"
    "    LANGUAGE sql IMMUTABLE STRICT\n"
    "    AS $$SELECT 'CC-' || "
    "lpad(n::text, greatest(6, length(n::text)), '0')$$"
)

_OLD_DEFAULT = (
    "('CC-'::text || lpad((nextval('item_code_seq'::regclass))::text, 6, '0'::text))"
)


def upgrade() -> None:
    """The function, then the default that calls it."""
    op.execute(_FUNCTION)
    op.execute(
        "ALTER TABLE inventory_item ALTER COLUMN item_code "
        "SET DEFAULT item_code_format(nextval('item_code_seq'::regclass))"
    )


def downgrade() -> None:
    """The six-character default back, then the function goes."""
    op.execute(
        f"ALTER TABLE inventory_item ALTER COLUMN item_code SET DEFAULT {_OLD_DEFAULT}"
    )
    op.execute("DROP FUNCTION item_code_format(bigint)")
