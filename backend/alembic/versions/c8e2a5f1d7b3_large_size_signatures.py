"""Signature pairs: a large-size note's Register of the Treasury.

A large-size note was signed by the Register of the Treasury and the
Treasurer, not the Treasurer and the Secretary, so Speelman / White could
not be recorded on a Series 1899 $1 Silver Certificate. A pair now has a
`register` or a `secretary`, never both; every row on file has a secretary.

A downgrade gives a pair with no secretary an empty one, so the column can
be required again; its label still names both signers.

Revision ID: c8e2a5f1d7b3
Revises: b9d4f2a6e8c1
Create Date: 2026-10-16 01:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8e2a5f1d7b3"
down_revision: str | None = "b9d4f2a6e8c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the register, and let a pair have one in place of a secretary."""
    op.add_column(
        "signature_combination",
        sa.Column("register", sa.String(length=128), nullable=True),
    )
    op.alter_column(
        "signature_combination",
        "secretary",
        existing_type=sa.String(length=128),
        nullable=True,
    )
    op.create_check_constraint(
        "ck_signature_combination_one_cosigner",
        "signature_combination",
        "(secretary IS NULL) <> (register IS NULL)",
    )


def downgrade() -> None:
    """Drop the register; a pair without a secretary gets an empty one."""
    op.drop_constraint(
        "ck_signature_combination_one_cosigner",
        "signature_combination",
        type_="check",
    )
    op.get_bind().execute(
        sa.text(
            "UPDATE signature_combination SET secretary = '' WHERE secretary IS NULL"
        )
    )
    op.alter_column(
        "signature_combination",
        "secretary",
        existing_type=sa.String(length=128),
        nullable=False,
    )
    op.drop_column("signature_combination", "register")
