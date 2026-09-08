"""rename cost columns and add series designation

`price` was the wrong name. It is what was paid for the item, while
`listing.price` is what the item is offered for -- one word for both sides of a
transaction, in a schema that will eventually join them in a profit report.

    price            -> item_cost
    shipping         -> shipping_cost
    taxes            -> sales_tax
    storage_quantity -> piece_count
    title            -> source_title

`sales_tax` because income tax on a realised gain is a different thing and this
system will have to speak about both. `piece_count` because it is not a fact
about storage -- `storage_form` and `storage_location` answer that -- but how
many objects the row stands for. `source_title` because the column holds
whatever the source called the row, which for this spreadsheet is the
denomination: "Rolls .25", "$20 Bill", "Duit", "2".

Also adds `currency_detail.series_designation`, generated: how a collector
writes the series, 1935A rather than 1935 and A in separate columns. Generated
rather than stored so the two forms cannot disagree.

**These are renames, not drops.** Autogenerate proposed DROP plus ADD, which
would have discarded every value in five columns across 7,591 rows.

The four views must be dropped first: a view stores its source columns by
reference, so renaming underneath one leaves it exposing the old name, and
these views are redefined anyway.

Revision ID: b78d71343405
Revises: 3bdcdea56f53
Create Date: 2026-09-08 09:05:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from app.models.views import DROP_VIEWS, create_views

revision: str = "b78d71343405"
down_revision: str | None = "3bdcdea56f53"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RENAMES: tuple[tuple[str, str], ...] = (
    ("price", "item_cost"),
    ("shipping", "shipping_cost"),
    ("taxes", "sales_tax"),
    ("storage_quantity", "piece_count"),
    ("title", "source_title"),
)

_SERIES_DESIGNATION = (
    "CASE WHEN series_year IS NULL THEN NULL "
    "ELSE series_year::text || coalesce(series_letter, '') END"
)


def upgrade() -> None:
    for statement in DROP_VIEWS:
        op.execute(statement)

    for old, new in RENAMES:
        op.alter_column("inventory_item", old, new_column_name=new)

    # Named for what they now guard.
    op.drop_constraint(
        "ck_inventory_item_price_non_negative", "inventory_item", type_="check"
    )
    op.create_check_constraint(
        "ck_inventory_item_cost_non_negative", "inventory_item", "item_cost >= 0"
    )
    op.drop_constraint(
        "ck_inventory_item_quantity_positive", "inventory_item", type_="check"
    )
    op.create_check_constraint(
        "ck_inventory_item_piece_count_positive", "inventory_item", "piece_count > 0"
    )

    op.add_column(
        "currency_detail",
        sa.Column(
            "series_designation",
            sa.String(length=16),
            sa.Computed(_SERIES_DESIGNATION, persisted=True),
            nullable=True,
        ),
    )

    for statement in create_views(soft_delete=False):
        op.execute(statement)


def downgrade() -> None:
    for statement in DROP_VIEWS:
        op.execute(statement)

    op.drop_column("currency_detail", "series_designation")

    op.drop_constraint(
        "ck_inventory_item_piece_count_positive", "inventory_item", type_="check"
    )
    op.drop_constraint(
        "ck_inventory_item_cost_non_negative", "inventory_item", type_="check"
    )

    # Drop everything before renaming, then create everything after: a
    # constraint expression names a column, so it can only be created once
    # that column exists under that name.
    for old, new in RENAMES:
        op.alter_column("inventory_item", new, new_column_name=old)

    op.create_check_constraint(
        "ck_inventory_item_quantity_positive", "inventory_item", "storage_quantity > 0"
    )
    op.create_check_constraint(
        "ck_inventory_item_price_non_negative", "inventory_item", "price >= 0"
    )

    # The pre-rename view text, so the downgrade leaves a consistent database.
    # soft_delete=False too: this downgrade runs after ffe36996607c's own
    # downgrade has already dropped deleted_at, so a view naming it here would
    # fail with UndefinedColumn.
    for statement in create_views(renamed_costs=False, soft_delete=False):
        op.execute(statement)
