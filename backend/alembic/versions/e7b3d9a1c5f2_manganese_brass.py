"""Metals: the golden dollars are manganese brass, not copper-nickel clad.

The Sacagawea, Presidential and American Innovation dollars are struck in
manganese brass over a copper core, but were filed under the one clad metal
and described as "Copper-Nickel Clad". This adds the metal and moves those
three designs' composition rows to it.

A composition row is identified by its metal among the rest
(`app.seeding.NATURAL_KEYS`), so the seed file's change alone would add a
brass row beside each clad one, and a coin covered by two rows has none:
the rows are moved here, and the seed then finds them unchanged. Items are
left to `app.classifier_defaults`, which moves a metal it filled itself.

The downgrade moves the rows back and removes the metal, refused while an
item records it.

Revision ID: e7b3d9a1c5f2
Revises: c4e9a2f7b1d8
Create Date: 2026-10-15 11:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7b3d9a1c5f2"
down_revision: str | None = "c4e9a2f7b1d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DESIGNS = ("sacagawea_dollar", "presidential_dollar", "american_innovation_dollar")

_MOVE = sa.text(
    "UPDATE composition SET metal_id = (SELECT id FROM metal WHERE code = :to)"
    " WHERE metal_id = (SELECT id FROM metal WHERE code = :frm)"
    " AND series_id IN (SELECT id FROM series WHERE code IN :designs)"
).bindparams(sa.bindparam("designs", expanding=True))


def upgrade() -> None:
    """Add manganese brass, and move the golden dollars' rows to it."""
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO metal (code, label, symbol, is_precious, sort_order,"
            " is_active, source) VALUES ('manganese_brass', 'Manganese Brass',"
            " '', false, 90, true, 'seeded') ON CONFLICT (code) DO NOTHING"
        )
    )
    bind.execute(_MOVE, {"to": "manganese_brass", "frm": "clad", "designs": _DESIGNS})


def downgrade() -> None:
    """Move the rows back to clad and remove the metal, unless an item holds it."""
    bind = op.get_bind()
    held = bind.execute(
        sa.text(
            "SELECT count(*) FROM inventory_item WHERE metal_id ="
            " (SELECT id FROM metal WHERE code = 'manganese_brass')"
        )
    ).scalar_one()
    if held:
        raise RuntimeError(
            f"{held} item(s) record manganese brass; change them before downgrading"
        )
    bind.execute(_MOVE, {"to": "clad", "frm": "manganese_brass", "designs": _DESIGNS})
    bind.execute(sa.text("DELETE FROM metal WHERE code = 'manganese_brass'"))
