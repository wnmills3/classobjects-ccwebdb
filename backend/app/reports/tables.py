"""The table aliases the report queries share, and the live rule on them.

`inventory_item` and `item_kind`, aliased `i` and `k` -- exactly how
`inventory_search`'s own queries alias them (`_J_KIND`) -- so SQL text
written for the search against those aliases (`MISSING_FIELDS[key].sql`)
can be reused in a report verbatim rather than restated against different
table names.

`LIVE` is `live_item()` (`app.live`), the shared live-row predicate, moved
onto the `i` alias: `live_item()` names `InventoryItem` unaliased, and
`ClauseAdapter` rewrites its column references onto `ITEM` so it composes
with a query that joins `inventory_item` as `i`.
"""

from __future__ import annotations

from sqlalchemy.sql.util import ClauseAdapter

from ..live import live_item
from ..models import InventoryItem, ItemKind

__all__ = ["ITEM", "KIND", "LIVE"]

ITEM = InventoryItem.__table__.alias("i")
KIND = ItemKind.__table__.alias("k")
LIVE = ClauseAdapter(ITEM).traverse(live_item())
