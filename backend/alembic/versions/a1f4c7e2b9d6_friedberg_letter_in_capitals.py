"""Friedberg numbers in capitals, all but a mule's `m`.

`app.fr_format.normalize_fr` now keeps the letter after the digits in
capitals (`12a` is kept as `12A`), all but a mule's `m`, which stays
lower-case whichever case it was typed in (`9901M` is kept as `9901m`).
Numbers already recorded are brought to the same form here, so a number
typed today finds the row recorded before.

Two recorded numbers that become one text (`12a` beside `12A`) are not
merged: the upgrade stops and names them, and nothing is changed. Settle
which one stands in the console, then upgrade again.

There is no downgrade of the text: the case each number was typed in is
not kept anywhere.

Revision ID: a1f4c7e2b9d6
Revises: b8e2d4f6a1c3
Create Date: 2026-10-14 20:30:00.000000

"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1f4c7e2b9d6"
down_revision: str | None = "b8e2d4f6a1c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Where a mule's `m` stands once the number is in capitals: as the letter
#: after the digits, or after the district. Copied from `app.fr_format`
#: rather than imported: a migration keeps the rule of its day.
_MULE_AFTER_DIGITS = re.compile(r"^(\d+)M", re.ASCII)
_MULE_AFTER_DISTRICT = re.compile(r"-([A-L])M", re.ASCII)


def _folded(number: str) -> str:
    """`number` in capitals, all but a mule's `m`."""
    upper = number.upper()
    upper = _MULE_AFTER_DIGITS.sub(r"\1m", upper)
    return _MULE_AFTER_DISTRICT.sub(r"-\1m", upper)


def upgrade() -> None:
    """Rewrite each number whose optional letter is not yet in its kept case."""
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, fr_number FROM friedberg_number ORDER BY id")
    ).all()
    targets: dict[str, list[str]] = {}
    for _, number in rows:
        targets.setdefault(_folded(number), []).append(number)
    clashes = sorted(
        " and ".join(sorted(group)) for group in targets.values() if len(group) > 1
    )
    if clashes:
        raise RuntimeError(
            "These Friedberg numbers would become one number; settle which "
            "stands in the console, then upgrade again: " + "; ".join(clashes)
        )
    for row_id, number in rows:
        if _folded(number) != number:
            conn.execute(
                sa.text(
                    "UPDATE friedberg_number SET fr_number = :n, updated_at = now() "
                    "WHERE id = :id"
                ),
                {"n": _folded(number), "id": row_id},
            )


def downgrade() -> None:
    """Leave the numbers as they are: the case they were typed in is gone."""
