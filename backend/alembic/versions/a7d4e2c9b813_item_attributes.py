"""Note attributes become item attributes, for coins and notes.

docs/specs/item-attributes-design.md, section 2. `note_attribute` becomes
`item_attribute`, with `applies_to` and an `attribute_group`; its 17 rows
keep their codes and meaning, as serial features of notes (four of them --
error, web press, specimen and proof notes -- as varieties).
`item_note_attribute` becomes `item_attribute_link`, which records where a
link came from and, once a person removes a derived one, that it was
removed. The existing links were all read by a machine -- from the rating
text or the serial -- so they are `derived`.

Downgrade drops the new columns and names; links a person removed are
deleted rather than brought back, and attributes that are not for notes
stay in the table.

Revision ID: a7d4e2c9b813
Revises: f3c5d8a91b20
Create Date: 2026-09-16 20:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7d4e2c9b813"
down_revision: str | None = "f3c5d8a91b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GROUP = postgresql.ENUM(
    "serial",
    "variety",
    "release",
    "verification",
    "qualifier",
    name="attribute_group",
    create_type=False,
)
_APPLIES = postgresql.ENUM(
    "coin", "currency", "any", name="applies_to", create_type=False
)
_SOURCE = postgresql.ENUM(
    "seeded", "derived", "manual", name="provenance_source", create_type=False
)

#: The shipped note attributes that are not serial features.
_VARIETIES = ("error", "web_press", "specimen", "proof")


def upgrade() -> None:
    """Rename the two tables, add the new columns, fill them."""
    _GROUP.create(op.get_bind(), checkfirst=False)

    op.rename_table("note_attribute", "item_attribute")
    op.execute("ALTER SEQUENCE note_attribute_id_seq RENAME TO item_attribute_id_seq")
    op.execute(
        "ALTER TABLE item_attribute "
        "RENAME CONSTRAINT note_attribute_pkey TO item_attribute_pkey"
    )
    op.execute(
        "ALTER TABLE item_attribute "
        "RENAME CONSTRAINT uq_note_attribute_code TO uq_item_attribute_code"
    )
    op.add_column(
        "item_attribute",
        sa.Column("applies_to", _APPLIES, nullable=False, server_default="currency"),
    )
    op.add_column(
        "item_attribute",
        sa.Column("attribute_group", _GROUP, nullable=False, server_default="serial"),
    )
    op.alter_column("item_attribute", "applies_to", server_default=None)
    op.alter_column("item_attribute", "attribute_group", server_default=None)
    op.execute(
        sa.text(
            "UPDATE item_attribute SET attribute_group = 'variety' "
            "WHERE code = ANY(:codes)"
        ).bindparams(codes=list(_VARIETIES))
    )
    op.create_index(
        "ix_item_attribute_attribute_group", "item_attribute", ["attribute_group"]
    )

    op.rename_table("item_note_attribute", "item_attribute_link")
    op.alter_column(
        "item_attribute_link", "note_attribute_id", new_column_name="item_attribute_id"
    )
    for old, new in (
        ("item_note_attribute_pkey", "item_attribute_link_pkey"),
        (
            "item_note_attribute_inventory_item_id_fkey",
            "item_attribute_link_inventory_item_id_fkey",
        ),
        (
            "item_note_attribute_note_attribute_id_fkey",
            "item_attribute_link_item_attribute_id_fkey",
        ),
    ):
        op.execute(f"ALTER TABLE item_attribute_link RENAME CONSTRAINT {old} TO {new}")
    op.add_column(
        "item_attribute_link",
        sa.Column("source", _SOURCE, nullable=False, server_default="derived"),
    )
    op.alter_column("item_attribute_link", "source", server_default=None)
    op.add_column(
        "item_attribute_link",
        sa.Column("derived_by", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "item_attribute_link",
        sa.Column("noted_by_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "item_attribute_link_noted_by_id_fkey",
        "item_attribute_link",
        "users",
        ["noted_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "item_attribute_link",
        sa.Column(
            "noted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.alter_column("item_attribute_link", "noted_at", server_default=None)
    op.add_column(
        "item_attribute_link",
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_item_attribute_link_item_attribute_id",
        "item_attribute_link",
        ["item_attribute_id"],
    )

    op.execute(
        "UPDATE reference_alias SET table_name = 'item_attribute' "
        "WHERE table_name = 'note_attribute'"
    )


def downgrade() -> None:
    """Back to note attributes; removed links are deleted, not restored."""
    op.execute(
        "UPDATE reference_alias SET table_name = 'note_attribute' "
        "WHERE table_name = 'item_attribute'"
    )
    op.execute("DELETE FROM item_attribute_link WHERE removed_at IS NOT NULL")
    op.drop_index(
        "ix_item_attribute_link_item_attribute_id", table_name="item_attribute_link"
    )
    op.drop_constraint(
        "item_attribute_link_noted_by_id_fkey",
        "item_attribute_link",
        type_="foreignkey",
    )
    for column in ("removed_at", "noted_at", "noted_by_id", "derived_by", "source"):
        op.drop_column("item_attribute_link", column)
    for old, new in (
        ("item_attribute_link_pkey", "item_note_attribute_pkey"),
        (
            "item_attribute_link_inventory_item_id_fkey",
            "item_note_attribute_inventory_item_id_fkey",
        ),
        (
            "item_attribute_link_item_attribute_id_fkey",
            "item_note_attribute_note_attribute_id_fkey",
        ),
    ):
        op.execute(f"ALTER TABLE item_attribute_link RENAME CONSTRAINT {old} TO {new}")
    op.alter_column(
        "item_attribute_link", "item_attribute_id", new_column_name="note_attribute_id"
    )
    op.rename_table("item_attribute_link", "item_note_attribute")

    op.drop_index("ix_item_attribute_attribute_group", table_name="item_attribute")
    op.drop_column("item_attribute", "attribute_group")
    op.drop_column("item_attribute", "applies_to")
    op.execute(
        "ALTER TABLE item_attribute "
        "RENAME CONSTRAINT uq_item_attribute_code TO uq_note_attribute_code"
    )
    op.execute(
        "ALTER TABLE item_attribute "
        "RENAME CONSTRAINT item_attribute_pkey TO note_attribute_pkey"
    )
    op.execute("ALTER SEQUENCE item_attribute_id_seq RENAME TO note_attribute_id_seq")
    op.rename_table("item_attribute", "note_attribute")
    _GROUP.drop(op.get_bind(), checkfirst=False)
