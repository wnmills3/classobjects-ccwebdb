"""Classifier defaults: note issues, aliases, per-field provenance.

Three tables for docs/specs/classifier-defaults-design.md:

- `note_issue`: the small-size issue facts a note's class, seal and
  signatures are looked up from. Filled by the seed file.
- `reference_alias`: other names for rows of any classifier table.
- `item_field_source`: which fields of an item hold a derived default.

Note types follow BEP's names. `national_currency` becomes
`national_bank_note` in place, and `legal_tender` is folded into `us_note`:
BEP's "legal tender" is the statutory term covering every class, not a class.
Any row pointing at `legal_tender` is repointed first (none did when this was
written). Both old names return as aliases through the seed file.

Existing derived values are recorded, so the editor can mark them and the
pass can refresh them -- but only where that cannot claim a person's value:

- `composition_id` on every item not entered by hand. No edit path sets it,
  so it is always the importer's.
- `series_id` on the same items, unless a person has confirmed the series:
  the importer never sets one, so it came from `series_match` or
  `series_classify` -- or from an edit, which leaves no other trace. Labelled
  `series_backfill` rather than claiming which pass it was.

Metal, fineness and weights are not marked. The importer's own values and a
person's later edits cannot be told apart here, and a marked field may be
overwritten when a year correction points at another composition. Imports
from now on record them as they are filled.

Revision ID: c3f8a61d7e24
Revises: b5e2c9d41a07
Create Date: 2026-09-16 12:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3f8a61d7e24"
down_revision: str | None = "b5e2c9d41a07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None



def upgrade() -> None:
    """Create the tables, align note types, record existing derived values."""
    op.create_table(
        "note_issue",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("denomination_id", sa.Integer(), nullable=False),
        sa.Column("series_year", sa.Integer(), nullable=False),
        sa.Column("series_letter", sa.String(length=1), nullable=True),
        sa.Column("note_type_id", sa.Integer(), nullable=False),
        sa.Column("seal_color_id", sa.Integer(), nullable=False),
        sa.Column("signature_combination_id", sa.Integer(), nullable=True),
        sa.Column("variant", sa.String(length=64), nullable=True),
        sa.Column("serial_prefix", sa.String(length=1), nullable=True),
        sa.ForeignKeyConstraint(
            ["denomination_id"],
            ["denomination.id"],
            name="fk_note_issue_denomination_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["note_type_id"],
            ["note_type.id"],
            name="fk_note_issue_note_type_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["seal_color_id"],
            ["seal_color.id"],
            name="fk_note_issue_seal_color_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["signature_combination_id"],
            ["signature_combination.id"],
            name="fk_note_issue_signature_combination_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "denomination_id",
            "series_year",
            "series_letter",
            "note_type_id",
            "seal_color_id",
            name="uq_note_issue",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        "ix_note_issue_lookup", "note_issue", ["denomination_id", "series_year"]
    )

    op.create_table(
        "reference_alias",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("table_name", sa.String(length=64), nullable=False),
        sa.Column("row_id", sa.Integer(), nullable=False),
        sa.Column("alias", sa.String(length=255), nullable=False),
        sa.UniqueConstraint("table_name", "row_id", "alias", name="uq_reference_alias"),
    )
    op.create_index(
        "ix_reference_alias_table_row", "reference_alias", ["table_name", "row_id"]
    )

    op.create_table(
        "item_field_source",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("inventory_item_id", sa.Integer(), nullable=False),
        sa.Column("field_name", sa.String(length=64), nullable=False),
        sa.Column("derived_by", sa.String(length=64), nullable=False),
        sa.Column("derived_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            ["inventory_item.id"],
            name="fk_item_field_source_inventory_item_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "inventory_item_id", "field_name", name="uq_item_field_source"
        ),
    )
    op.create_index(
        "ix_item_field_source_inventory_item_id",
        "item_field_source",
        ["inventory_item_id"],
    )

    op.execute(
        "UPDATE note_type SET code = 'national_bank_note', "
        "label = 'National Bank Note' WHERE code = 'national_currency'"
    )
    for table in ("currency_detail", "friedberg_number"):
        op.execute(
            f"UPDATE {table} SET note_type_id = "
            "(SELECT id FROM note_type WHERE code = 'us_note') "
            "WHERE note_type_id = "
            "(SELECT id FROM note_type WHERE code = 'legal_tender')"
        )
    op.execute("DELETE FROM note_type WHERE code = 'legal_tender'")

    op.execute(
        "INSERT INTO item_field_source "
        "(inventory_item_id, field_name, derived_by, derived_at) "
        "SELECT i.id, 'series_id', 'series_backfill', now() FROM inventory_item i "
        "WHERE i.series_id IS NOT NULL AND i.source <> 'manual' "
        "AND NOT EXISTS (SELECT 1 FROM item_field_review r "
        "WHERE r.inventory_item_id = i.id AND r.field_name = 'series_id')"
    )
    op.execute(
        "INSERT INTO item_field_source "
        "(inventory_item_id, field_name, derived_by, derived_at) "
        "SELECT id, 'composition_id', 'composition', now() FROM inventory_item "
        "WHERE composition_id IS NOT NULL AND source <> 'manual'"
    )


def downgrade() -> None:
    """Drop the tables and restore the two note-type codes."""
    op.drop_index(
        "ix_item_field_source_inventory_item_id", table_name="item_field_source"
    )
    op.drop_table("item_field_source")
    op.drop_index("ix_reference_alias_table_row", table_name="reference_alias")
    op.drop_table("reference_alias")
    op.drop_index("ix_note_issue_lookup", table_name="note_issue")
    op.drop_table("note_issue")

    op.execute(
        "UPDATE note_type SET code = 'national_currency', "
        "label = 'National Currency' WHERE code = 'national_bank_note'"
    )
    op.execute(
        "INSERT INTO note_type (code, label, sort_order, is_active, source) "
        "SELECT 'legal_tender', 'Legal Tender Note', 60, true, 'seeded' "
        "WHERE NOT EXISTS (SELECT 1 FROM note_type WHERE code = 'legal_tender')"
    )
