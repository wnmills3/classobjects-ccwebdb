"""Named, kind-aware diagnostics over the inventory.

An anomaly is a check with a name, not a generic field filter.
`issue=no_grade` means *a coin or banknote with no grade*, because bullion has
no grade by nature and 712 rounds have no weight either -- a generic
`grade=null` would bury 2,965 real cases under rounds that will never have
one. The domain knowledge belongs here, written once, rather than in the head
of whoever types the filter.

Each check appears three ways from this one definition: a filter
(`?issue=no_year`), a count returned with the page so the size of a job is
visible before committing to it, and a badge on the row. Adding a check later
means adding one entry.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["COIN_ISSUES", "CURRENCY_ISSUES", "SHARED_ISSUES", "Issue"]


@dataclass(frozen=True)
class Issue:
    """One diagnostic: the predicate that finds it, and the joins it needs.

    `sql` is parenthesised by the caller before being ANDed with the rest of
    the query, so a predicate containing OR is safe to write here plainly.
    """

    sql: str
    join: tuple[str, ...] = ()
    #: What the check means, for the filter panel. Not a label for the code:
    #: the code is the stable contract and appears in bookmarked URLs.
    description: str = ""


#: Joins duplicated from inventory_search rather than imported, to keep the
#: import one-way: the search module owns the query, this module only supplies
#: predicates. The strings must match exactly, since deduplication compares
#: whole clauses.
_J_CUR_DETAIL = "LEFT JOIN currency_detail cud ON cud.inventory_item_id = i.id"


SHARED_ISSUES: dict[str, Issue] = {
    "no_year": Issue(
        "i.year_start IS NULL",
        description="No year recorded",
    ),
    "no_country": Issue(
        "i.country_id IS NULL",
        description="No country recorded",
    ),
    "no_grade": Issue(
        "i.grade_id IS NULL AND k.code IN ('coin', 'currency')",
        description="A coin or banknote with no grade; bullion is excluded",
    ),
    "no_denomination": Issue(
        "i.denomination_id IS NULL AND k.code IN ('coin', 'currency')",
        description="A coin or banknote with no denomination",
    ),
    "kind_unknown": Issue(
        "k.code = 'unknown'",
        description="The import could not classify it",
    ),
    "zero_cost": Issue(
        "coalesce(i.item_cost, 0) = 0",
        description="No cost recorded, or zero",
    ),
    "mixed_marker": Issue(
        "i.grade_raw ILIKE '%mixed%' OR i.description ILIKE '%mixed%'",
        # Deliberately not folded into no_grade. `Mixed` means "known to
        # vary", which is a positive statement that the row stands for several
        # different coins -- so the remedy is to decompose the lot, not to
        # fill in the field.
        description="Known to vary: the row stands for several different items",
    ),
    "unreviewed": Issue(
        "NOT EXISTS (SELECT 1 FROM item_field_review r "
        "WHERE r.inventory_item_id = i.id)",
        description="Nobody has confirmed any field by examination",
    ),
}


COIN_ISSUES: dict[str, Issue] = {
    **SHARED_ISSUES,
    "no_weight_bullion": Issue(
        "i.fine_weight_ozt IS NULL AND k.code = 'bullion'",
        description="Bullion with no weight; its value cannot be computed",
    ),
    "repeated_identity": Issue(
        "EXISTS (SELECT 1 FROM item_certification c "
        "WHERE c.inventory_item_id = i.id AND c.cert_number IN ("
        "SELECT cert_number FROM item_certification "
        "WHERE coalesce(cert_number, '') <> '' "
        "GROUP BY cert_number HAVING count(*) > 1))",
        # Candidates only, never merged. Three different problems look alike
        # here -- the same item entered twice, one purchase recorded twice,
        # and a year parsed into the cert field -- and only the shape of the
        # value tells them apart, so a person decides.
        description="A certification number that appears on more than one row",
    ),
}


CURRENCY_ISSUES: dict[str, Issue] = {
    **SHARED_ISSUES,
    "star_mismatch": Issue(
        "coalesce(cud.serial_number LIKE '*%' OR cud.serial_number LIKE '%*', false) "
        "<> EXISTS (SELECT 1 FROM item_note_attribute x "
        "JOIN note_attribute na ON na.id = x.note_attribute_id "
        "WHERE x.inventory_item_id = i.id AND na.code = 'star')",
        join=(_J_CUR_DETAIL,),
        # Visible only as a disagreement between two fields, which is what
        # makes it valuable: neither field looks wrong alone. Star notes carry
        # a premium, so a wrong attribute either overprices a note or sells a
        # star note as an ordinary one.
        description="The serial and the star attribute disagree",
    ),
    "malformed_serial": Issue(
        "cud.serial_number ~ '[0-9][A-Z][0-9]'",
        join=(_J_CUR_DETAIL,),
        # A warning, never a refusal. Three of the first four serials flagged
        # by this rule were valid notes it had not anticipated.
        description="An interior letter in the serial; usually a typo, sometimes real",
    ),
    "repeated_identity": Issue(
        "cud.serial_number IN (SELECT serial_number FROM currency_detail "
        "WHERE coalesce(serial_number, '') <> '' "
        "GROUP BY serial_number HAVING count(*) > 1)",
        join=(_J_CUR_DETAIL,),
        # Four of the nine groups in this collection are legitimate: matched
        # serials across issues are a deliberate pursuit. Candidates, not
        # errors.
        description="A serial that appears on more than one note",
    ),
    "near_duplicate_serial": Issue(
        # Exact matching is not enough. Three duplicates in this collection
        # hide behind single-character errors -- `O` for `U`, a dropped digit,
        # `6` for `3` -- and are invisible to equality.
        #
        # Scoped to one purchase order, which is both where they cluster and
        # what keeps this affordable: the comparison is quadratic within a
        # group and the largest order holds 85 items. Collection-wide it would
        # be 1,015 x 1,015 and would find mostly noise, because two unrelated
        # notes differing by one digit are two unrelated notes.
        "EXISTS (SELECT 1 FROM currency_detail o "
        "JOIN inventory_item oi ON oi.id = o.inventory_item_id "
        "WHERE oi.id <> i.id "
        "AND oi.purchase_order_id = i.purchase_order_id "
        "AND i.purchase_order_id IS NOT NULL "
        "AND coalesce(o.serial_number, '') <> '' "
        "AND coalesce(cud.serial_number, '') <> '' "
        "AND levenshtein("
        "upper(regexp_replace(o.serial_number, '[^A-Za-z0-9]', '', 'g')), "
        "upper(regexp_replace(cud.serial_number, '[^A-Za-z0-9]', '', 'g'))"
        ") = 1)",
        join=(_J_CUR_DETAIL,),
        description=(
            "A serial one character from another in the same order; "
            "usually a mistranscription"
        ),
    ),
}
