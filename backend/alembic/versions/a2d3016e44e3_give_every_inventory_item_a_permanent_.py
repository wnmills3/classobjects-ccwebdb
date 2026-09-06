"""give every inventory item a permanent code

Revision ID: a2d3016e44e3
Revises: 7e6c16c1e0eb
Create Date: 2026-09-06 14:41:47.681010

Adds `inventory_item.item_code`: the permanent, unique, human-writable
identifier for one physical object.

Done in four steps rather than one, because the column is NOT NULL and the
table already holds rows. Adding a NOT NULL column with a `nextval` default to
a populated table works, but doing it explicitly makes the backfill order --
and therefore which item got which code -- deliberate rather than incidental.
Existing items are numbered by id, so the oldest acquisition gets the lowest
code.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'a2d3016e44e3'
down_revision: str | None = '7e6c16c1e0eb'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CODE_DEFAULT = "'CC-' || lpad(nextval('item_code_seq')::text, 6, '0')"


def upgrade() -> None:
    # 1. The sequence that issues codes. Sequences never reuse a value, so a
    #    deleted item's code is never handed to a later one -- which is what
    #    makes the code safe to cite in an audit years afterwards.
    op.execute("CREATE SEQUENCE IF NOT EXISTS item_code_seq START 1")

    # 2. Nullable to begin with, so the existing rows are not rejected.
    op.add_column(
        "inventory_item",
        sa.Column("item_code", sa.String(length=32), nullable=True),
    )

    # 3. Backfill in id order: the first item acquired gets CC-000001.
    #
    #    Numbered with row_number() rather than nextval(). PostgreSQL does not
    #    promise to evaluate nextval() in the order of an ORDER BY inside a
    #    subquery, so using it here would assign codes in an arbitrary order
    #    that merely looked deliberate.
    op.execute(
        """
        WITH ordered AS (
            SELECT id, row_number() OVER (ORDER BY id) AS rn FROM inventory_item
        )
        UPDATE inventory_item AS i
           SET item_code = 'CC-' || lpad(o.rn::text, 6, '0')
          FROM ordered AS o
         WHERE i.id = o.id
        """
    )

    #    Advance the sequence past everything just issued, so the next item
    #    created does not collide with a backfilled code.
    op.execute(
        "SELECT setval('item_code_seq', "
        "GREATEST((SELECT count(*) FROM inventory_item), 1))"
    )

    # 4. Now it can be required, unique and defaulted.
    op.alter_column(
        "inventory_item",
        "item_code",
        nullable=False,
        server_default=sa.text(CODE_DEFAULT),
    )
    op.create_unique_constraint(
        "uq_inventory_item_code", "inventory_item", ["item_code"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_inventory_item_code", "inventory_item", type_="unique")
    op.drop_column("inventory_item", "item_code")
    # Dropping the sequence too: leaving it behind would mean a later upgrade
    # resumed numbering mid-way and issued codes that look like they belong to
    # items that never existed.
    op.execute("DROP SEQUENCE IF EXISTS item_code_seq")
