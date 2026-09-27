"""Sellers: who sold a purchase on the marketplace its vendor names.

The vendor is usually the marketplace -- ebay.com, whatnot.com -- and the
seller on it was one link per purchase (`purchase_order.seller_url`). A
seller is now a row of its own, with a name and a store link, that many
purchases name. Each link already recorded becomes a seller named from it
(`/usr/<name>`, `/str/<name>`, or the last part of its path), one seller per
link, and the column goes.

Revision ID: c3e8a1f05b72
Revises: b7d2f4a91c36
Create Date: 2026-09-27 15:00:00.000000

"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3e8a1f05b72"
down_revision: str | None = "b7d2f4a91c36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _name_from(link: str) -> str:
    """A seller's name read from their store link: the profile or store name."""
    path = re.sub(r"^https?://[^/]+", "", link).split("?")[0].split("#")[0]
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2 and parts[0].lower() in {"usr", "str", "user", "shop", "seller"}:
        return parts[1]
    if parts:
        return parts[-1]
    host = re.match(r"^https?://([^/]+)", link)
    return host.group(1) if host else link


def upgrade() -> None:
    """The table, each purchase's link moved into it, and the column dropped."""
    op.create_table(
        "seller",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("store_url", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_seller_name"),
    )
    op.add_column("purchase_order", sa.Column("seller_id", sa.Integer(), nullable=True))
    op.create_index("ix_purchase_order_seller_id", "purchase_order", ["seller_id"])
    op.create_foreign_key(
        "purchase_order_seller_id_fkey",
        "purchase_order",
        "seller",
        ["seller_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    conn = op.get_bind()
    links = conn.execute(
        sa.text(
            "select distinct seller_url from purchase_order "
            "where seller_url is not null order by seller_url"
        )
    ).scalars()
    taken: set[str] = set()
    for link in links:
        base = _name_from(link)
        name, n = base, 2
        while name.casefold() in taken:
            name, n = f"{base} ({n})", n + 1
        taken.add(name.casefold())
        seller_id = conn.execute(
            sa.text(
                "insert into seller (name, store_url, created_at, updated_at) "
                "values (:n, :u, now(), now()) returning id"
            ),
            {"n": name, "u": link},
        ).scalar_one()
        conn.execute(
            sa.text("update purchase_order set seller_id = :s where seller_url = :u"),
            {"s": seller_id, "u": link},
        )
    op.drop_column("purchase_order", "seller_url")


def downgrade() -> None:
    """Each purchase's seller back to a link of its own, and the table gone."""
    op.add_column(
        "purchase_order", sa.Column("seller_url", sa.String(1000), nullable=True)
    )
    op.execute(
        "update purchase_order po set seller_url = s.store_url "
        "from seller s where s.id = po.seller_id"
    )
    op.drop_constraint("purchase_order_seller_id_fkey", "purchase_order", type_="foreignkey")
    op.drop_index("ix_purchase_order_seller_id", table_name="purchase_order")
    op.drop_column("purchase_order", "seller_id")
    op.drop_table("seller")
