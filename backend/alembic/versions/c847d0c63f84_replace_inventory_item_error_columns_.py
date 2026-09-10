"""replace inventory item error columns with item_error

An item may carry more than one mint or printing error -- miscut and
overprint are routinely found on the same bill -- and a single
`error_type_id` foreign key on `inventory_item` cannot express that. This
replaces it and `error_details` with `item_error`, one row per error, so an
item with two errors gets two rows instead of contending for one column.

Zero rows use either column today: `error_type_id` is NULL everywhere and
`error_details` is empty everywhere, so there is nothing to migrate.

`coin_inventory` and `currency_inventory` both select `error_type_id`, so
they are dropped and recreated from the current (post-drop) definitions --
a view stores its source columns by reference, the same reason every other
migration that touches `inventory_item`'s columns also touches the views.

Revision ID: c847d0c63f84
Revises: 9cc26d4f74b5
Create Date: 2026-09-10 12:12:49.477318

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from app.models.views import CREATE_VIEWS, DROP_VIEWS
from sqlalchemy.dialects import postgresql

revision: str = 'c847d0c63f84'
down_revision: str | None = '9cc26d4f74b5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The type already exists -- every other classifier table uses it.
#: Declaring it with create_type=False references it instead of trying to
#: CREATE TYPE a second time, which fails.
_PROVENANCE = postgresql.ENUM(
    "seeded", "derived", "manual", name="provenance_source", create_type=False
)


#: The pre-migration shape of the two views that named the dropped columns,
#: for `downgrade()` -- verbatim `coin_inventory` / `currency_inventory` text
#: as it stood the revision before this one. A literal snapshot rather than a
#: derivation: `views.py`'s fragment-stripping system exists to strip *later*
#: columns out of the *current* definitions for an *earlier* revision, and
#: every case so far has been a column added later. This is the opposite
#: direction -- a column removed -- so there is nothing in the current
#: definitions to strip it from; it has to be kept somewhere, and here is
#: that somewhere.
_COIN_INVENTORY_WITH_ERROR_TYPE = """
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
    et.code           AS error_type,
    i.error_details,
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
LEFT JOIN error_type et   ON et.id = i.error_type_id
LEFT JOIN metal mt        ON mt.id = i.metal_id
WHERE i.split_at IS NULL
  AND i.deleted_at IS NULL
  AND k.code IN ('coin', 'bullion', 'set', 'medal', 'token')
"""

_CURRENCY_INVENTORY_WITH_ERROR_TYPE = """
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
    et.code           AS error_type,
    i.error_details,
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
LEFT JOIN error_type et       ON et.id = i.error_type_id
WHERE i.split_at IS NULL
  AND i.deleted_at IS NULL
  AND k.code = 'currency'
"""

#: `item_valuation` and `public_catalog` never named either dropped column,
#: so only the two inventory views need a pre-migration variant; the other
#: two are recreated from the current (unchanged) definitions in `CREATE_VIEWS`.
_CREATE_VIEWS_WITH_ERROR_TYPE: tuple[str, ...] = (
    _COIN_INVENTORY_WITH_ERROR_TYPE,
    _CURRENCY_INVENTORY_WITH_ERROR_TYPE,
    *CREATE_VIEWS[2:],
)


def upgrade() -> None:
    for statement in DROP_VIEWS:
        op.execute(statement)

    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('item_error',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('inventory_item_id', sa.Integer(), nullable=False),
    sa.Column('error_type_id', sa.Integer(), nullable=False),
    sa.Column('details', sa.Text(), nullable=True),
    sa.Column('source', _PROVENANCE, nullable=False),
    sa.Column('noted_by_id', sa.Integer(), nullable=True),
    sa.Column('noted_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['error_type_id'], ['error_type.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_item.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['noted_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('inventory_item_id', 'error_type_id', name='uq_item_error_item_type')
    )
    op.create_index(op.f('ix_item_error_error_type_id'), 'item_error', ['error_type_id'], unique=False)
    op.create_index(op.f('ix_item_error_inventory_item_id'), 'item_error', ['inventory_item_id'], unique=False)
    op.drop_index(op.f('ix_inventory_item_error_type_id'), table_name='inventory_item')
    op.drop_constraint(op.f('inventory_item_error_type_id_fkey'), 'inventory_item', type_='foreignkey')
    op.drop_column('inventory_item', 'error_type_id')
    op.drop_column('inventory_item', 'error_details')
    # ### end Alembic commands ###

    for statement in CREATE_VIEWS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DROP_VIEWS:
        op.execute(statement)

    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('inventory_item', sa.Column('error_details', sa.TEXT(), autoincrement=False, nullable=True))
    op.add_column('inventory_item', sa.Column('error_type_id', sa.INTEGER(), autoincrement=False, nullable=True))
    op.create_foreign_key(op.f('inventory_item_error_type_id_fkey'), 'inventory_item', 'error_type', ['error_type_id'], ['id'], ondelete='RESTRICT')
    op.create_index(op.f('ix_inventory_item_error_type_id'), 'inventory_item', ['error_type_id'], unique=False)
    op.drop_index(op.f('ix_item_error_inventory_item_id'), table_name='item_error')
    op.drop_index(op.f('ix_item_error_error_type_id'), table_name='item_error')
    op.drop_table('item_error')
    # ### end Alembic commands ###

    # The pre-migration view text, so the downgrade leaves a consistent
    # database rather than two views naming columns that no longer exist.
    for statement in _CREATE_VIEWS_WITH_ERROR_TYPE:
        op.execute(statement)
