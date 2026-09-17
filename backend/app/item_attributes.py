"""An item's attributes: reading them, and a person setting them.

docs/specs/item-attributes-design.md, section 2. Attributes are a
many-to-many link (`item_attribute_link`), and each link says where it came
from: `derived` when a rule or the importer read it, `manual` when a person
set it.

**A removed attribute stays removed.** The serial patterns, and the
attribute rules after them, would put a deleted link straight back, so a
person removing one marks the row (`removed_at`) and it stays; every reader
skips it, every rule leaves it alone. That holds for a link a person added
too: deleting it would let a rule add it back the moment the person had
taken it away. Setting a removed attribute again clears the mark and keeps
the link's source.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    AppliesTo,
    InventoryItem,
    ItemAttribute,
    ItemAttributeLink,
    ItemKind,
    ProvenanceSource,
    utcnow,
)

__all__ = [
    "AttributeRefused",
    "Held",
    "held_attributes",
    "set_attributes",
]


class AttributeRefused(ValueError):
    """Attribute codes that cannot be set on this item, with the reason."""


@dataclass(frozen=True)
class Held:
    """One attribute an item carries, as the API returns it."""

    code: str
    label: str
    group: str
    source: str
    derived_by: str | None


def held_attributes(db: Session, item_id: int) -> list[Held]:
    """The item's attributes, less those a person removed, in vocabulary order."""
    rows = db.execute(
        select(ItemAttribute, ItemAttributeLink)
        .join(
            ItemAttributeLink, ItemAttributeLink.item_attribute_id == ItemAttribute.id
        )
        .where(
            ItemAttributeLink.inventory_item_id == item_id,
            ItemAttributeLink.removed_at.is_(None),
        )
        .order_by(ItemAttribute.sort_order, ItemAttribute.code)
    ).all()
    return [
        Held(
            code=attribute.code,
            label=attribute.label,
            group=attribute.attribute_group.value,
            source=link.source.value,
            derived_by=link.derived_by,
        )
        for attribute, link in rows
    ]


def _allowed(db: Session, item: InventoryItem) -> set[AppliesTo]:
    kind = db.scalar(select(ItemKind.code).where(ItemKind.id == item.item_kind_id))
    own = AppliesTo.currency if kind == "currency" else AppliesTo.coin
    return {own, AppliesTo.any}


def set_attributes(
    db: Session, item: InventoryItem, codes: Iterable[str], *, user_id: int | None
) -> bool:
    """Make the item's attributes exactly `codes`; True if anything changed.

    Refused, before anything is written, for a code that is unknown, retired,
    or for the other kind of item (a star note on a coin).
    """
    wanted = list(dict.fromkeys(codes))
    found = {
        row.code: row
        for row in db.scalars(
            select(ItemAttribute).where(
                ItemAttribute.code.in_(wanted), ItemAttribute.is_active.is_(True)
            )
        )
    }
    unknown = [code for code in wanted if code not in found]
    if unknown:
        raise AttributeRefused(f"Unknown attribute(s): {unknown}")
    allowed = _allowed(db, item)
    wrong = [code for code in wanted if found[code].applies_to not in allowed]
    if wrong:
        kind = "note" if AppliesTo.currency in allowed else "coin"
        raise AttributeRefused(f"Not an attribute of a {kind}: {wrong}")

    links = {
        link.item_attribute_id: link
        for link in db.scalars(
            select(ItemAttributeLink).where(
                ItemAttributeLink.inventory_item_id == item.id
            )
        )
    }
    wanted_ids = {found[code].id for code in wanted}
    changed = False

    for attribute_id, link in links.items():
        if attribute_id in wanted_ids or link.removed_at is not None:
            continue
        changed = True
        link.removed_at = utcnow()

    for attribute_id in wanted_ids:
        existing = links.get(attribute_id)
        if existing is not None and existing.removed_at is None:
            continue
        changed = True
        if existing is not None:
            existing.removed_at = None
        else:
            db.add(
                ItemAttributeLink(
                    inventory_item_id=item.id,
                    item_attribute_id=attribute_id,
                    source=ProvenanceSource.manual,
                    noted_by_id=user_id,
                )
            )
    db.flush()
    return changed
