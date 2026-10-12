"""Gift: the attribute of a banknote that cost nothing.

docs/specs/item-attributes-design.md, section 2, "Attributes from facts: Gift".
A note entered or saved in the item editor at an item cost of $0 is a gift,
so the save gives it the attribute, takes the word out of its title -- the
attribute says it now -- and writes the description again where the
description was the generated one.

The link is a derived one (`derived_by` `no_cost`). Like every rule's, it is
added only where the note has no link for the attribute at all, so a person
who removes Gift keeps it removed, and it is taken back only while it is
still the rule's own: a note given a cost later loses the Gift this rule
gave it and keeps one a person set.
"""

from __future__ import annotations

import re
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .item_descriptions import suggested_description
from .models import (
    AppliesTo,
    InventoryItem,
    ItemAttribute,
    ItemAttributeLink,
    ProvenanceSource,
)

__all__ = ["GIFT", "RULE", "apply", "earns", "would_add"]

#: The attribute's code.
GIFT = "gift"

#: `item_attribute_link.derived_by` for the links this rule adds.
RULE = "no_cost"

#: The word in a title, with the white space and dashes that set it apart.
_WORD = re.compile(r"[\s\-:,]*\bgift\b[\s\-:,]*", re.IGNORECASE)


def earns(item: InventoryItem) -> bool:
    """Whether the item is a banknote that cost nothing."""
    return item.currency_detail is not None and item.item_cost == Decimal("0")


def _attribute(db: Session) -> ItemAttribute | None:
    """The Gift attribute, where the vocabulary has it for notes and in use."""
    return db.scalar(
        select(ItemAttribute).where(
            ItemAttribute.code == GIFT,
            ItemAttribute.is_active.is_(True),
            ItemAttribute.applies_to.in_([AppliesTo.currency, AppliesTo.any]),
        )
    )


def _link(
    db: Session, item: InventoryItem, attribute: ItemAttribute
) -> ItemAttributeLink | None:
    """The item's link for the attribute, a removed one included."""
    return db.get(ItemAttributeLink, (item.id, attribute.id))


def would_add(db: Session, item: InventoryItem) -> bool:
    """Whether a save of the item as it stands would give it Gift."""
    attribute = _attribute(db)
    return attribute is not None and earns(item) and _link(db, item, attribute) is None


def without_the_word(title: str) -> str:
    """A title less the word Gift; unchanged where nothing else would be left."""
    kept = _WORD.sub(" ", title).strip()
    return kept or title


def apply(db: Session, item: InventoryItem) -> list[str]:
    """Bring the item's Gift in step with its cost; the editor fields it moved.

    Called by a save once everything else is written and flushed. Returns
    the names, as the change log knows them, of what this changed:
    `attributes`, `description`, `source_title`.

    The description is written again only when the link changes and the one
    held is empty or is the one generated before the change, so a person's
    own words are never replaced.
    """
    attribute = _attribute(db)
    if attribute is None:
        return []
    moved: list[str] = []
    link = _link(db, item, attribute)
    free = earns(item)
    held = link is not None and link.removed_at is None
    adds = free and link is None
    takes_back = not free and held and link is not None and link.derived_by == RULE
    if adds or takes_back:
        generated = suggested_description(db, item)
        if adds:
            db.add(
                ItemAttributeLink(
                    inventory_item_id=item.id,
                    item_attribute_id=attribute.id,
                    source=ProvenanceSource.derived,
                    derived_by=RULE,
                )
            )
        else:
            db.delete(link)
        db.flush()
        held = adds
        moved.append("attributes")
        if (item.description or "").strip() in ("", generated):
            item.description = suggested_description(db, item)
            moved.append("description")
    if free and held and item.source_title:
        title = without_the_word(item.source_title)
        if title != item.source_title:
            item.source_title = title
            moved.append("source_title")
    return moved
