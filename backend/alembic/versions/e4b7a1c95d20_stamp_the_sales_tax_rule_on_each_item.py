"""stamp the sales-tax rule on each item

The sales-tax rate and whether shipping is taxed become settings --
SALES_TAX_RATE and SALES_TAX_INCLUDES_SHIPPING -- copied onto each item when
it is created. This adds the per-row `tax_includes_shipping` column the second
one needs, and makes both generated columns honour it.

Every existing row is backfilled with `true`. The old expression taxed
shipping unconditionally, so `true` reproduces every stored figure exactly.

`tax_rate` loses its `0.0635` server default. The rate now comes from the
setting through the ORM, and a copy baked into the schema would be a second
source of truth -- one a raw INSERT would silently use.

PostgreSQL 17 added `ALTER COLUMN ... SET EXPRESSION`, so both generated
columns are rewritten in place. Dropping and re-adding them would mean first
dropping every view that reads them.

The expressions are written out here rather than imported from
`app.models.core`: a migration is a dated document, and importing the current
definition would silently change what this revision means the next time the
model changes.

A downgrade taxes shipping on every row again, including any recorded as not
taxing it -- the column that said otherwise no longer exists to consult.

Revision ID: e4b7a1c95d20
Revises: c847d0c63f84
Create Date: 2026-09-10 15:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e4b7a1c95d20'
down_revision: str | None = 'c847d0c63f84'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TAX_BEFORE = "round((item_cost + shipping_cost) * tax_rate, 2)"
_TAX_AFTER = (
    "round((item_cost + CASE WHEN tax_includes_shipping THEN shipping_cost "
    "ELSE 0 END) * tax_rate, 2)"
)


def _set_expressions(tax: str) -> None:
    """Point both generated columns at `tax`; the total repeats it inline."""
    op.execute(
        f"ALTER TABLE inventory_item ALTER COLUMN sales_tax SET EXPRESSION AS ({tax})"
    )
    op.execute(
        "ALTER TABLE inventory_item ALTER COLUMN total_cost "
        f"SET EXPRESSION AS (item_cost + shipping_cost + {tax})"
    )


def upgrade() -> None:
    """Add the per-row shipping rule, and make the tax honour it."""
    op.add_column(
        'inventory_item',
        sa.Column(
            'tax_includes_shipping',
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
    )
    # The server default existed only to backfill the rows already present.
    op.alter_column('inventory_item', 'tax_includes_shipping', server_default=None)
    op.alter_column('inventory_item', 'tax_rate', server_default=None)
    _set_expressions(_TAX_AFTER)


def downgrade() -> None:
    """Restore the unconditional expression before dropping what it reads."""
    _set_expressions(_TAX_BEFORE)
    op.alter_column('inventory_item', 'tax_rate', server_default=sa.text('0.0635'))
    op.drop_column('inventory_item', 'tax_includes_shipping')
