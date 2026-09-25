"""Database views.

They present coins and currency as independent inventories with their own
columns while nothing shared is implemented twice.

**Nothing in the application reads them.** They are maintained and unread: a
grep for these four names across `backend/app` finds only comments, and the
frontend none at all. `app.inventory_search` explains in its own docstring
why it queries the base tables instead -- the joins cost about thirty times
the time -- and the shop's responses are built by
`routers.catalog.to_catalog_item`, field by field, in Python.

``public_catalog`` therefore **describes** an authorization boundary rather
than enforcing one. It must never expose storage location, local catalog
numbers, cost basis, or inventory photographs, and a test asserts that
against `PUBLIC_CATALOG_FORBIDDEN_COLUMNS` so a later ``select *`` cannot
quietly widen the view. But no request passes through it, so what actually
keeps those columns away from a buyer is `to_catalog_item` plus
`CatalogItemOut` -- see `test_catalogue_never_exposes_cost_basis_or_location`,
which is the enforcement rather than a second copy of it.

Keeping the view honest is still worth doing: it is the schema's statement of
which columns are public, and anything later built on it inherits that.

Views are not part of ``Base.metadata`` -- Alembic autogenerate reflects tables
only. The baseline migration holds them as they stand; a migration that
changes one runs `DROP_VIEWS` and then `CREATE_VIEWS` from the definitions
here, and the tests build their database the same way.
"""

from __future__ import annotations

__all__ = [
    "ALL_VIEWS",
    "CREATE_VIEWS",
    "DROP_VIEWS",
    "PUBLIC_CATALOG_FORBIDDEN_COLUMNS",
]

#: Columns that must never appear in `public_catalog`. Asserted by a test.
#:
#: Note what is absent: no image column appears here, although the module
#: docstring's promise includes inventory photographs. The view selects
#: none today, so that half of the promise holds by accident rather than
#: by this assertion. Add the image columns here if the view ever grows
#: one.
PUBLIC_CATALOG_FORBIDDEN_COLUMNS: frozenset[str] = frozenset(
    {
        "storage_location_id",
        "storage_location",
        "local_catalog_number",
        "item_cost",
        "shipping_cost",
        "sales_tax",
        "tax_rate",
        "tax_includes_shipping",
        "total_cost",
        "numismatic_value",
        "purchase_order_id",
        "vendor_id",
        "rating",
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
    stk.code          AS strike_type,
    grade_display(
        stk.prefix, stk.suffix, g.numeric_value, g.is_plus, g.label,
        gsc.code = 'sheldon'
    ) AS grade,
    g.numeric_value   AS grade_value,
    g.grade_rank,
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
    i.weight_note,
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
LEFT JOIN grade_scale gsc ON gsc.id = g.grade_scale_id
LEFT JOIN strike_type stk ON stk.id = i.strike_type_id
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


# The authorization boundary. Every column here is deliberate; nothing about
# where an item is stored, what it cost, or what it is cataloged as internally
# may appear. See PUBLIC_CATALOG_FORBIDDEN_COLUMNS and its test.
#
# Its WHERE clause states the shop's rule -- own store, fixed price, active --
# a third time, plus three guards the Python twin does not need. This one
# cannot be shared: a database view is SQL text created by a migration, so it
# can call neither `offering_writes.sellable_in_shop` nor
# `shop_listing_filters`. If the three-part rule changes, this clause changes
# with them -- do not delete the extra guards to "keep it in sync", they are
# not part of that rule:
#   - `quantity_available > 0` is deliberate, not drift: this view is the
#     authorization boundary and never shows a sold-out entry, while
#     `GET /api/catalog` exposes `in_stock` so the shop can choose to. Checkout
#     enforces stock itself.
#   - `split_at IS NULL` and `deleted_at IS NULL` guard states the rest of the
#     code already makes unreachable (splitting ends every listing on the
#     parent; `delete_item` refuses while any listing row exists) -- kept here
#     because this view has no other code path to lean on for that.
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
    grade_display(
        stk.prefix, stk.suffix, g.numeric_value, g.is_plus, g.label,
        gsc.code = 'sheldon'
    ) AS grade,
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
JOIN sales_venue sv       ON sv.id = l.sales_venue_id
LEFT JOIN denomination d  ON d.id  = i.denomination_id
LEFT JOIN country c       ON c.id  = i.country_id
LEFT JOIN grade g         ON g.id  = i.grade_id
LEFT JOIN grade_scale gsc ON gsc.id = g.grade_scale_id
LEFT JOIN strike_type stk ON stk.id = i.strike_type_id
LEFT JOIN grading_service gs ON gs.id = i.grading_service_id
LEFT JOIN bullion_form bf ON bf.id = i.bullion_form_id
LEFT JOIN metal mt        ON mt.id = i.metal_id
WHERE l.is_active
  AND sv.is_own_store
  AND l.format = 'fixed_price'
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
