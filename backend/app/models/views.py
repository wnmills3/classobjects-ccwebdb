"""Database views.

The API and UI read these rather than the base tables, so coins and currency
present as independent inventories with their own columns while nothing shared
is implemented twice.

``public_catalog`` is an **authorisation boundary**, not a convenience. It must
never expose storage location, local catalogue numbers, cost basis, or
inventory photographs. That is asserted by tests against the column list, so a
later ``select *`` cannot quietly widen it.

Views are not part of ``Base.metadata`` -- Alembic autogenerate reflects tables
only -- so they are created and dropped explicitly by the migration that owns
them, from the definitions here.
"""

from __future__ import annotations

__all__ = [
    "ALL_VIEWS",
    "CREATE_VIEWS",
    "CREATE_VIEWS_ORIGINAL",
    "CREATE_VIEWS_WITHOUT_LINEAGE",
    "DROP_VIEWS",
    "PUBLIC_CATALOG_FORBIDDEN_COLUMNS",
    "create_views",
]

#: Columns that must never appear in `public_catalog`. Asserted by a test.
#:
#: The cost columns are listed under **both** names. The old ones cannot
#: appear any more, so on their own this set would have quietly stopped
#: guarding anything -- a test that can no longer fail. They stay because a
#: downgrade recreates the pre-rename view, and that view must be checked too.
PUBLIC_CATALOG_FORBIDDEN_COLUMNS: frozenset[str] = frozenset(
    {
        "storage_location_id",
        "storage_location",
        "local_catalog_number",
        "item_cost",
        "shipping_cost",
        "sales_tax",
        "price",
        "shipping",
        "tax_rate",
        "taxes",
        "total_cost",
        "numismatic_value",
        "purchase_order_id",
        "vendor_id",
        "notes_raw",
        "parent_item_id",
    }
)


_COIN_INVENTORY = """
CREATE VIEW coin_inventory AS
SELECT
    i.id,
    i.source_title,
    i.description,
    k.code            AS item_kind,
    d.code            AS denomination,
    d.label           AS denomination_label,
    bf.code           AS bullion_form,
    sf.code           AS set_form,
    stf.code          AS storage_form,
    i.piece_count,
    c.code            AS country,
    i.year_start,
    i.year_end,
    m.mark            AS mint_mark,
    m.label           AS mint,
    cd.variety,
    cd.pcgs_type_id,
    cd.pcgs_status,
    g.code            AS grade,
    g.numeric_value   AS grade_value,
    gd.code           AS grade_designation,
    gs.code           AS grading_service,
    a.code            AS authenticity,
    st.code           AS status,
    disp.code         AS disposition,
    i.item_code,
    i.parent_item_id,
    i.split_at,
    i.storage_location_id,
    i.local_catalog_number,
    i.item_cost,
    i.shipping_cost,
    i.sales_tax,
    i.total_cost,
    i.numismatic_value,
    vb.code           AS valuation_basis,
    mt.code           AS metal,
    i.fineness,
    i.gross_weight_ozt,
    i.fine_weight_ozt,
    i.weight_raw,
    i.attributes,
    i.created_at,
    i.updated_at
FROM inventory_item i
JOIN item_kind k          ON k.id  = i.item_kind_id
JOIN storage_form stf     ON stf.id = i.storage_form_id
JOIN authenticity a       ON a.id  = i.authenticity_id
JOIN item_status st       ON st.id = i.status_id
JOIN disposition disp     ON disp.id = i.disposition_id
JOIN valuation_basis vb   ON vb.id = i.valuation_basis_id
LEFT JOIN coin_detail cd  ON cd.inventory_item_id = i.id
LEFT JOIN denomination d  ON d.id  = i.denomination_id
LEFT JOIN bullion_form bf ON bf.id = i.bullion_form_id
LEFT JOIN set_form sf     ON sf.id = i.set_form_id
LEFT JOIN country c       ON c.id  = i.country_id
LEFT JOIN mint m          ON m.id  = cd.mint_id
LEFT JOIN grade g         ON g.id  = i.grade_id
LEFT JOIN grade_designation gd ON gd.id = i.grade_designation_id
LEFT JOIN grading_service gs   ON gs.id = i.grading_service_id
LEFT JOIN metal mt        ON mt.id = i.metal_id
WHERE i.split_at IS NULL
  AND i.deleted_at IS NULL
  AND k.code IN ('coin', 'bullion', 'set', 'medal', 'token')
"""
# The `split_at IS NULL` above excludes lots that have been broken into
# pieces. Without it the lot and its pieces are both counted, so the
# collection appears to hold twice what it does and to have cost twice what it
# did. The note lives here rather than as a SQL comment because PostgreSQL
# stores comments inside the view definition it reflects back.


_CURRENCY_INVENTORY = """
CREATE VIEW currency_inventory AS
SELECT
    i.id,
    i.source_title,
    i.description,
    k.code            AS item_kind,
    d.code            AS denomination,
    d.label           AS denomination_label,
    stf.code          AS storage_form,
    i.piece_count,
    c.code            AS country,
    i.year_start,
    i.year_end,
    nt.code           AS note_type,
    cud.series_year,
    cud.series_letter,
    sc.code           AS seal_color,
    fd.letter         AS fed_district_letter,
    fd.city           AS fed_district_city,
    cud.serial_number,
    cud.friedberg_id,
    fr.fr_number      AS friedberg_number,
    cud.friedberg_status,
    sig.treasurer,
    sig.secretary,
    g.code            AS grade,
    g.numeric_value   AS grade_value,
    gd.code           AS grade_designation,
    gs.code           AS grading_service,
    a.code            AS authenticity,
    st.code           AS status,
    disp.code         AS disposition,
    i.item_code,
    i.parent_item_id,
    i.split_at,
    i.storage_location_id,
    i.local_catalog_number,
    i.item_cost,
    i.shipping_cost,
    i.sales_tax,
    i.total_cost,
    i.numismatic_value,
    vb.code           AS valuation_basis,
    i.attributes,
    i.created_at,
    i.updated_at
FROM inventory_item i
JOIN item_kind k              ON k.id  = i.item_kind_id
JOIN storage_form stf         ON stf.id = i.storage_form_id
JOIN authenticity a           ON a.id  = i.authenticity_id
JOIN item_status st           ON st.id = i.status_id
JOIN disposition disp         ON disp.id = i.disposition_id
JOIN valuation_basis vb       ON vb.id = i.valuation_basis_id
LEFT JOIN currency_detail cud ON cud.inventory_item_id = i.id
LEFT JOIN denomination d      ON d.id  = i.denomination_id
LEFT JOIN country c           ON c.id  = i.country_id
LEFT JOIN note_type nt        ON nt.id = cud.note_type_id
LEFT JOIN seal_color sc       ON sc.id = cud.seal_color_id
LEFT JOIN fed_district fd     ON fd.id = cud.fed_district_id
LEFT JOIN signature_combination sig ON sig.id = cud.signature_combination_id
LEFT JOIN friedberg_number fr ON fr.id = cud.friedberg_id
LEFT JOIN grade g             ON g.id  = i.grade_id
LEFT JOIN grade_designation gd ON gd.id = i.grade_designation_id
LEFT JOIN grading_service gs  ON gs.id = i.grading_service_id
WHERE i.split_at IS NULL
  AND i.deleted_at IS NULL
  AND k.code = 'currency'
"""


# Melt value is computed, never stored: spot price is an input and it moves.
# The latest quote per metal comes from DISTINCT ON, which PostgreSQL answers
# from the (metal_id, quoted_at DESC) index with one backwards scan per metal.
_ITEM_VALUATION = """
CREATE VIEW item_valuation AS
WITH latest_spot AS (
    SELECT DISTINCT ON (metal_id)
        metal_id,
        price_per_ozt,
        quoted_at
    FROM metal_price
    ORDER BY metal_id, quoted_at DESC
),
computed AS (
    SELECT
        i.id                    AS inventory_item_id,
        i.item_kind_id,
        i.total_cost,
        i.numismatic_value,
        i.piece_count,
        i.fine_weight_ozt,
        vb.code                 AS valuation_basis,
        s.price_per_ozt         AS spot_price_used,
        s.quoted_at             AS spot_quoted_at,
        CASE
            WHEN i.fine_weight_ozt IS NULL OR s.price_per_ozt IS NULL THEN NULL
            ELSE round(i.fine_weight_ozt * s.price_per_ozt * i.piece_count, 2)
        END                     AS melt_value
    FROM inventory_item i
    JOIN valuation_basis vb ON vb.id = i.valuation_basis_id
    LEFT JOIN latest_spot s ON s.metal_id = i.metal_id
    WHERE i.split_at IS NULL
      AND i.deleted_at IS NULL
)
SELECT
    c.*,
    CASE c.valuation_basis
        WHEN 'melt'       THEN c.melt_value
        WHEN 'numismatic' THEN c.numismatic_value
        WHEN 'manual'     THEN c.numismatic_value
    END AS reported_value,
    CASE c.valuation_basis
        WHEN 'melt'       THEN c.melt_value
        WHEN 'numismatic' THEN c.numismatic_value
        WHEN 'manual'     THEN c.numismatic_value
    END - c.total_cost AS profit,
    (
        CASE c.valuation_basis
            WHEN 'melt'       THEN c.melt_value
            WHEN 'numismatic' THEN c.numismatic_value
            WHEN 'manual'     THEN c.numismatic_value
        END - c.total_cost
    ) / nullif(c.total_cost, 0) AS profit_pct
FROM computed c
"""


# The authorisation boundary. Every column here is deliberate; nothing about
# where an item is stored, what it cost, or what it is catalogued as internally
# may appear. See PUBLIC_CATALOG_FORBIDDEN_COLUMNS and its test.
_PUBLIC_CATALOG = """
CREATE VIEW public_catalog AS
SELECT
    l.id                AS listing_id,
    l.price             AS listing_price,
    cur.code            AS listing_currency,
    l.quantity_available,
    l.listed_at,
    COALESCE(NULLIF(l.title, ''), i.source_title)      AS title,
    COALESCE(NULLIF(l.description, ''), i.description) AS description,
    k.code              AS item_kind,
    d.label             AS denomination_label,
    c.label             AS country,
    i.year_start,
    i.year_end,
    g.code              AS grade,
    gs.code             AS grading_service,
    bf.label            AS bullion_form,
    mt.code             AS metal,
    i.fineness,
    i.gross_weight_ozt,
    i.fine_weight_ozt
FROM listing l
JOIN inventory_item i     ON i.id = l.inventory_item_id
JOIN item_kind k          ON k.id = i.item_kind_id
JOIN currency cur         ON cur.id = l.currency_id
LEFT JOIN denomination d  ON d.id  = i.denomination_id
LEFT JOIN country c       ON c.id  = i.country_id
LEFT JOIN grade g         ON g.id  = i.grade_id
LEFT JOIN grading_service gs ON gs.id = i.grading_service_id
LEFT JOIN bullion_form bf ON bf.id = i.bullion_form_id
LEFT JOIN metal mt        ON mt.id = i.metal_id
WHERE l.is_active
  AND l.quantity_available > 0
  AND i.split_at IS NULL
  AND i.deleted_at IS NULL
"""


#: View names in creation order. Dropping walks this in reverse.
ALL_VIEWS: tuple[str, ...] = (
    "coin_inventory",
    "currency_inventory",
    "item_valuation",
    "public_catalog",
)

CREATE_VIEWS: tuple[str, ...] = (
    _COIN_INVENTORY,
    _CURRENCY_INVENTORY,
    _ITEM_VALUATION,
    _PUBLIC_CATALOG,
)

DROP_VIEWS: tuple[str, ...] = tuple(
    f"DROP VIEW IF EXISTS {name}" for name in reversed(ALL_VIEWS)
)


# ---------------------------------------------------------------------------
# Historical variants, for migrations
#
# A migration that creates these views imports the SQL from here, which means
# the SQL can change underneath a migration written months ago. A view created
# by an early revision must only name columns that existed at that revision --
# otherwise a fresh `upgrade head` fails on a column that has not been added
# yet, which is exactly what happened when `item_code` was introduced.
#
# So the variants are derived from the current definitions by removing the
# later columns, and every removal is asserted. Deriving rather than
# duplicating keeps them from drifting; asserting means a future edit that
# changes the wording fails loudly here instead of silently producing SQL that
# still names a column the target revision does not have.
# ---------------------------------------------------------------------------

_LINEAGE_FRAGMENTS: tuple[tuple[str, str], ...] = (
    ("    i.parent_item_id,\n", ""),
    ("    i.split_at,\n", ""),
    ("WHERE i.split_at IS NULL\n  AND ", "WHERE "),
    ("    WHERE i.split_at IS NULL\n", ""),
    ("\n  AND i.split_at IS NULL", ""),
)

_ITEM_CODE_FRAGMENTS: tuple[tuple[str, str], ...] = (("    i.item_code,\n", ""),)

#: The cost columns were renamed later -- price -> item_cost and friends -- so
#: a view created by an earlier revision must still say the old names. Without
#: this a fresh `upgrade head` fails partway: the view is created before the
#: rename runs, naming columns the table does not have yet.
_PRE_RENAME_FRAGMENTS: tuple[tuple[str, str], ...] = (
    ("    i.item_cost,\n", "    i.price,\n"),
    ("    i.shipping_cost,\n", "    i.shipping,\n"),
    ("    i.sales_tax,\n", "    i.taxes,\n"),
    ("    i.piece_count,\n", "    i.storage_quantity,\n"),
    ("    i.source_title,\n", "    i.title,\n"),
    ("        i.piece_count,\n", "        i.storage_quantity,\n"),
    (
        "i.fine_weight_ozt * s.price_per_ozt * i.piece_count",
        "i.fine_weight_ozt * s.price_per_ozt * i.storage_quantity",
    ),
    (
        "COALESCE(NULLIF(l.title, ''), i.source_title)      AS title",
        "COALESCE(NULLIF(l.title, ''), i.title)             AS title",
    ),
)

#: The names that rename introduced, asserted absent from a pre-rename view.
_RENAMED_COLUMNS = (
    "item_cost",
    "shipping_cost",
    "sales_tax",
    "piece_count",
    "source_title",
)

#: Soft delete came later than the views, so a view created by an earlier
#: revision must not name the column. Without this a fresh `upgrade head`
#: fails partway: the view is created before the column is added.
#:
#: Two indents because `item_valuation` nests its WHERE inside a CTE. They
#: cannot match each other's text: after `\n`, one expects exactly two spaces
#: then `AND`, the other six.
_SOFT_DELETE_FRAGMENTS: tuple[tuple[str, str], ...] = (
    ("\n  AND i.deleted_at IS NULL", ""),
    ("\n      AND i.deleted_at IS NULL", ""),
)


def _removal_fragments(
    *,
    lineage: bool,
    item_code: bool,
    renamed_costs: bool,
    soft_delete: bool,
) -> list[tuple[str, str]]:
    """The (fragment, replacement) pairs that strip the features not wanted.

    Order matters, which is why this is a list rather than a set.
    """
    removals: list[tuple[str, str]] = []
    # Soft delete is stripped FIRST, before lineage. Replacements apply in
    # list order, and the lineage fragment is
    # ("WHERE i.split_at IS NULL\n  AND ", "WHERE ") -- which would otherwise
    # swallow the `AND i.deleted_at IS NULL` line now sitting directly beneath
    # it, leaving `WHERE i.deleted_at IS NULL` in a view whose revision has no
    # such column. The assertion in _assert_stripped then fires, and the fix is
    # this ordering rather than a wider fragment.
    if not soft_delete:
        removals.extend(_SOFT_DELETE_FRAGMENTS)

    if not lineage:
        removals.extend(_LINEAGE_FRAGMENTS)
    if not item_code:
        removals.extend(_ITEM_CODE_FRAGMENTS)
    if not renamed_costs:
        removals.extend(_PRE_RENAME_FRAGMENTS)
    return removals


def _assert_stripped(
    statement: str,
    *,
    lineage: bool,
    item_code: bool,
    renamed_costs: bool,
    soft_delete: bool,
) -> None:
    """Fail loudly if a fragment stopped matching the view SQL.

    Silence here would mean a migration creating a view that names a column
    which does not exist at its revision -- a failure at deploy time rather
    than at import time.
    """
    if not lineage:
        assert "split_at" not in statement, (
            "the lineage-stripping fragments no longer match the view SQL; "
            "a migration would create a view naming a column that does not "
            "exist at its revision"
        )
        assert "parent_item_id" not in statement
    if not item_code:
        assert "item_code" not in statement
    if not renamed_costs:
        for column in _RENAMED_COLUMNS:
            assert column not in statement, (
                f"{column} survived the pre-rename rewrite; a "
                "migration would create a view naming a column that "
                "does not exist at its revision"
            )
    if not soft_delete:
        assert "deleted_at" not in statement, (
            "the soft-delete-stripping fragments no longer match the view "
            "SQL; a migration would create a view naming a column that "
            "does not exist at its revision"
        )


def create_views(
    *,
    lineage: bool = True,
    item_code: bool = True,
    renamed_costs: bool = True,
    soft_delete: bool = True,
) -> tuple[str, ...]:
    """The view SQL as it stood before the named columns were introduced.

    ``lineage`` covers `parent_item_id` and `split_at`; ``item_code`` covers
    the permanent item code; ``renamed_costs`` covers the earlier names of
    `item_cost`, `shipping_cost`, `sales_tax`, `piece_count` and
    `source_title` (`price`, `shipping`, `taxes`, `storage_quantity`,
    `title`); ``soft_delete`` covers `deleted_at` and its `WHERE` clause in
    all four views. All four default to True, the current definitions, so a
    migration strips only what it names.
    """
    removals = _removal_fragments(
        lineage=lineage,
        item_code=item_code,
        renamed_costs=renamed_costs,
        soft_delete=soft_delete,
    )

    statements = []
    for statement in CREATE_VIEWS:
        for fragment, replacement in removals:
            statement = statement.replace(fragment, replacement)
        _assert_stripped(
            statement,
            lineage=lineage,
            item_code=item_code,
            renamed_costs=renamed_costs,
            soft_delete=soft_delete,
        )
        statements.append(statement)
    return tuple(statements)


#: What the views looked like before lot lineage existed. Used by the
#: downgrade of the migration that added it.
CREATE_VIEWS_WITHOUT_LINEAGE: tuple[str, ...] = create_views(
    lineage=False, renamed_costs=False, soft_delete=False
)

#: What they looked like when first created, before either addition.
CREATE_VIEWS_ORIGINAL: tuple[str, ...] = create_views(
    lineage=False, item_code=False, renamed_costs=False, soft_delete=False
)
