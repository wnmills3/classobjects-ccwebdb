"""Retire the one-time spreadsheet import (issue #1).

The database is the record, kept through the website and the workbook
backup (`app.workbook_backup`); the import that first filled it will never
run again. This drops its staging tables (`import_batch`, `import_row`,
`import_issue`) and the columns that only held spreadsheet text
(`notes_raw`, `denom_raw`, `year_raw`, `item_certification.raw`), renames
the two whose text is the owner's own and still used -- `grade_raw` to
`rating`, `weight_raw` to `weight_note` -- and clears the status notes the
import wrote.

The downgrade restores the schema only -- empty tables and columns, the old
names -- so the chain still runs backwards; nothing it dropped comes back.

Revision ID: 5b3a9e0c7d12
Revises: e2b8c4d6f1a3
Create Date: 2026-09-24 19:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.views import DROP_VIEWS, create_views

revision: str = "5b3a9e0c7d12"
down_revision: str | None = "e2b8c4d6f1a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The staging tables as they stood at e2b8c4d6f1a3, for the downgrade only:
#: the earlier revisions' own downgrades drop them by these names.
_STAGING_TABLES = """
CREATE TABLE import_batch (
    id bigserial PRIMARY KEY,
    source_path varchar(1024) NOT NULL,
    source_kind varchar(32) NOT NULL,
    sha256 varchar(64) NOT NULL,
    profile_name varchar(64) NOT NULL,
    mode varchar(16) NOT NULL,
    row_count integer NOT NULL,
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    notes text
);
CREATE INDEX ix_import_batch_sha256 ON import_batch (sha256);
CREATE TABLE import_row (
    id bigserial PRIMARY KEY,
    batch_id bigint NOT NULL REFERENCES import_batch(id) ON DELETE CASCADE,
    row_number integer NOT NULL,
    raw jsonb NOT NULL,
    status varchar(16) NOT NULL,
    item_kind varchar(32),
    subtype varchar(64),
    classified_by_rule varchar(64),
    inventory_item_id integer,
    CONSTRAINT uq_import_row_batch_number UNIQUE (batch_id, row_number),
    CONSTRAINT fk_import_row_inventory_item FOREIGN KEY (inventory_item_id)
        REFERENCES inventory_item(id) ON DELETE SET NULL
);
CREATE INDEX ix_import_row_batch_id ON import_row (batch_id);
CREATE INDEX ix_import_row_inventory_item_id ON import_row (inventory_item_id);
CREATE INDEX ix_import_row_kind ON import_row (item_kind);
CREATE INDEX ix_import_row_status ON import_row (status);
CREATE TABLE import_issue (
    id bigserial PRIMARY KEY,
    import_row_id bigint NOT NULL REFERENCES import_row(id) ON DELETE CASCADE,
    rule varchar(64) NOT NULL,
    severity varchar(16) NOT NULL,
    column_name varchar(64),
    raw_value varchar(512),
    proposed varchar(512),
    note text,
    resolved_by varchar(255),
    resolved_at timestamptz
);
CREATE INDEX ix_import_issue_import_row_id ON import_issue (import_row_id);
CREATE INDEX ix_import_issue_raw_value ON import_issue (raw_value);
CREATE INDEX ix_import_issue_rule ON import_issue (rule);
CREATE INDEX ix_import_issue_severity ON import_issue (severity);
"""


def upgrade() -> None:
    """Drop the import's tables and columns; rename the two kept columns."""
    for statement in DROP_VIEWS:
        op.execute(statement)
    op.drop_table("import_issue")
    op.drop_table("import_row")
    op.drop_table("import_batch")
    for column in ("notes_raw", "denom_raw", "year_raw"):
        op.drop_column("inventory_item", column)
    op.alter_column("inventory_item", "grade_raw", new_column_name="rating")
    op.alter_column("inventory_item", "weight_raw", new_column_name="weight_note")
    op.drop_column("item_certification", "raw")
    op.execute(
        sa.text("UPDATE item_status_history SET note = NULL WHERE note = 'set at import'")
    )
    for statement in create_views():
        op.execute(statement)


def downgrade() -> None:
    """Restore the schema as it was, empty: nothing dropped comes back."""
    for statement in DROP_VIEWS:
        op.execute(statement)
    op.add_column("item_certification", sa.Column("raw", sa.Text(), nullable=True))
    op.alter_column("inventory_item", "weight_note", new_column_name="weight_raw")
    op.alter_column("inventory_item", "rating", new_column_name="grade_raw")
    for column in ("notes_raw", "denom_raw", "year_raw"):
        op.add_column("inventory_item", sa.Column(column, sa.Text(), nullable=True))
    op.get_bind().exec_driver_sql(_STAGING_TABLES)
    for statement in create_views(renamed_notes=False):
        op.execute(statement)
