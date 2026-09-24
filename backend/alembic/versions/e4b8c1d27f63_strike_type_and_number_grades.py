"""Strike type split from the grade; grades become numbers.

docs/specs/item-attributes-design.md, decisions 1-3. ``PR69+`` becomes strike
type ``proof`` with grade ``69+``; ``MS65`` becomes ``business`` with ``65``.
Adjectival grades take the bottom of their standard range -- UNC and BU 60,
one plus (Choice) 63, two pluses (Gem) 65; PROOF 63, Gem Proof 65; AU 55, XF
40, VF 20, F 12, VG 8, G 4 -- and note adjectival grades the bottom of the
note scale's. Circulated and Ungraded stay as they are.

Every item is moved to its new grade (and strike type, if it had none) and
the old grade rows are removed. The rating as written stays in `grade_raw`.

Downgrade recomposes MS/PR/AU... codes from strike type and number. It cannot
restore an adjectival grade: an item that was BU comes back as MS60.

The mapping is written out here rather than imported from `app.grades`, so
this revision does not change when that module does.

Revision ID: e4b8c1d27f63
Revises: d9a2e47b1c05
Create Date: 2026-09-16 15:00:00.000000

"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.models.views import DROP_VIEWS, create_views

revision: str = "e4b8c1d27f63"
down_revision: str | None = "d9a2e47b1c05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCE = postgresql.ENUM(
    "seeded", "derived", "manual", name="provenance_source", create_type=False
)

_STRIKE_TYPES = (
    # code, label, prefix, suffix, sort_order
    ("business", "Business Strike", None, None, 10),
    ("proof", "Proof", "PR", None, 20),
    ("specimen", "Specimen", "SP", None, 30),
    ("reverse_proof", "Reverse Proof", "PR", "Reverse Proof", 40),
    ("enhanced_reverse_proof", "Enhanced Reverse Proof", "PR", "Enhanced Reverse Proof", 50),
    ("sms", "Special Mint Set", None, "SMS", 60),
)

#: The grade numbers the seed file lists, and those it lists with a plus.
_SEEDED_NUMBERS = frozenset(
    {70, 69, 68, 67, 66, 65, 64, 63, 62, 61, 60, 58, 55, 53, 50, 45, 40, 35, 30,
     25, 20, 15, 12, 10, 8, 6, 4, 3, 2, 1}
)
_SEEDED_PLUS = frozenset({45, 50, 53, 55, 58, 62, 63, 64, 65, 66, 67, 68, 69})

_COMPOUND = re.compile(r"^(MS|PR|PF|SP|AU|XF|EF|VF|VG|AG|FR|PO|F|G|P)-?(\d{1,2})(\+*)$")
_PREFIX_STRIKE = {"PR": "proof", "PF": "proof", "SP": "specimen"}
_ADJECTIVAL = {
    "CHOICE_UNC": ("business", 63),
    "CHOICE_BU": ("business", 63),
    "GEM_UNC": ("business", 65),
    "GEM_BU": ("business", 65),
    "PROOF": ("proof", 63),
    "CHOICE_PROOF": ("proof", 63),
    "GEM_PROOF": ("proof", 65),
    "AU": ("business", 55),
    "XF": ("business", 40),
    "VF": ("business", 20),
    "F": ("business", 12),
    "VG": ("business", 8),
    "G": ("business", 4),
}
_LADDER = {"UNC": (60, 63, 65), "BU": (60, 63, 65)}
_NOTE_ADJECTIVAL = {
    "N_GEM_UNC": "N65",
    "N_CHOICE_UNC": "N63",
    "N_UNC": "N60",
    "N_AU": "N50",
    "N_XF": "N40",
    "N_VF": "N20",
    "N_F": "N12",
    "N_VG": "N8",
    "N_G": "N4",
}


def _split(code: str) -> tuple[str | None, int | None, bool, str] | None:
    """(strike, number, plus, note code) for an old grade code, or None."""
    if code in _NOTE_ADJECTIVAL:
        return None, None, False, _NOTE_ADJECTIVAL[code]
    if match := _COMPOUND.match(code):
        prefix, number, pluses = match.groups()
        return _PREFIX_STRIKE.get(prefix, "business"), int(number), bool(pluses), ""
    base = code.rstrip("+")
    pluses = len(code) - len(base)
    if base in _LADDER:
        return "business", _LADDER[base][min(pluses, 2)], False, ""
    if base in _ADJECTIVAL:
        strike, number = _ADJECTIVAL[base]
        return strike, number, pluses > 0, ""
    return None


def _grade_display_sql() -> str:
    return """
CREATE OR REPLACE FUNCTION grade_display(
    strike_prefix text,
    strike_suffix text,
    numeric_value integer,
    is_plus boolean,
    label text,
    sheldon boolean
) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN NOT sheldon OR numeric_value IS NULL THEN label
        ELSE COALESCE(strike_prefix, CASE
                WHEN numeric_value >= 60 THEN 'MS'
                WHEN numeric_value >= 50 THEN 'AU'
                WHEN numeric_value >= 40 THEN 'XF'
                WHEN numeric_value >= 20 THEN 'VF'
                WHEN numeric_value >= 12 THEN 'F'
                WHEN numeric_value >= 8 THEN 'VG'
                WHEN numeric_value >= 4 THEN 'G'
                WHEN numeric_value = 3 THEN 'AG'
                WHEN numeric_value = 2 THEN 'FR'
                ELSE 'PO'
            END)
            || numeric_value::text
            || CASE WHEN is_plus THEN '+' ELSE '' END
            || COALESCE(' ' || strike_suffix, '')
    END
$$
"""


def upgrade() -> None:
    """Add strike types and number grades, and move every item across."""
    for statement in DROP_VIEWS:
        op.execute(statement)

    op.create_table(
        "strike_type",
        sa.Column("prefix", sa.String(length=16), nullable=True),
        sa.Column("suffix", sa.String(length=64), nullable=True),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("source", _SOURCE, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_strike_type_code"),
    )
    op.add_column(
        "grade",
        sa.Column("is_plus", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "grade",
        sa.Column(
            "grade_rank",
            sa.Numeric(precision=4, scale=1),
            sa.Computed(
                "numeric_value + CASE WHEN is_plus THEN 0.5 ELSE 0 END", persisted=True
            ),
            nullable=True,
        ),
    )
    # Dropped before any grade row is added: it is NOT NULL with no default,
    # and the strike type now says what it said.
    op.drop_column("grade", "is_proof")
    op.add_column(
        "inventory_item", sa.Column("strike_type_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "inventory_item_strike_type_id_fkey",
        "inventory_item",
        "strike_type",
        ["strike_type_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_inventory_item_strike_type_id", "inventory_item", ["strike_type_id"]
    )
    op.execute(_grade_display_sql())

    conn = op.get_bind()
    for code, label, prefix, suffix, order in _STRIKE_TYPES:
        conn.execute(
            sa.text(
                "INSERT INTO strike_type (code, label, prefix, suffix, sort_order, "
                "is_active, source) VALUES (:c, :l, :p, :s, :o, true, 'seeded')"
            ),
            {"c": code, "l": label, "p": prefix, "s": suffix, "o": order},
        )
    strikes = dict(conn.execute(sa.text("SELECT code, id FROM strike_type")).all())
    scales = dict(conn.execute(sa.text("SELECT code, id FROM grade_scale")).all())
    grades = {
        code: gid for code, gid in conn.execute(sa.text("SELECT code, id FROM grade"))
    }

    def grade_id(code: str, number: int | None, plus: bool, scale: str) -> int:
        if code in grades:
            return grades[code]
        seeded = scale == "sheldon" and (
            (not plus and number in _SEEDED_NUMBERS)
            or (plus and number in _SEEDED_PLUS)
        )
        new_id = conn.execute(
            sa.text(
                "INSERT INTO grade (code, label, grade_scale_id, numeric_value, "
                "is_plus, sort_order, is_active, source) VALUES "
                "(:c, :c, :s, :n, :p, :o, true, CAST(:src AS provenance_source)) "
                "RETURNING id"
            ),
            {
                "c": code,
                "s": scales.get(scale),
                "n": number,
                "p": plus,
                "o": 100 + (70 - (number or 0)) * 2 - (1 if plus else 0),
                "src": "seeded" if seeded else "derived",
            },
        ).scalar_one()
        grades[code] = new_id
        return new_id

    obsolete = []
    for old_id, old_code in conn.execute(
        sa.text("SELECT id, code FROM grade ORDER BY id")
    ).all():
        parts = _split(old_code)
        if parts is None:
            continue
        strike, number, plus, note_code = parts
        if note_code:
            target = grades.get(note_code)
            if target is None:
                continue  # not seeded yet; the seed load adds note grades
            conn.execute(
                sa.text("UPDATE inventory_item SET grade_id = :t WHERE grade_id = :o"),
                {"t": target, "o": old_id},
            )
        else:
            assert number is not None and strike is not None
            new_code = f"{number}{'+' if plus else ''}"
            target = grade_id(new_code, number, plus, "sheldon")
            conn.execute(
                sa.text(
                    "UPDATE inventory_item SET grade_id = :t, "
                    "strike_type_id = COALESCE(strike_type_id, :s) "
                    "WHERE grade_id = :o"
                ),
                {"t": target, "s": strikes[strike], "o": old_id},
            )
        if target != old_id:
            obsolete.append(old_id)
    if obsolete:
        conn.execute(
            sa.text(
                "DELETE FROM grade WHERE id = ANY(:ids) AND NOT EXISTS "
                "(SELECT 1 FROM inventory_item i WHERE i.grade_id = grade.id)"
            ),
            {"ids": obsolete},
        )

    for statement in create_views(renamed_notes=False, selling=False):
        op.execute(statement)


def downgrade() -> None:
    """Recompose MS/PR/AU... codes; adjectival grades are not restored."""
    for statement in DROP_VIEWS:
        op.execute(statement)
    op.add_column(
        "grade",
        sa.Column("is_proof", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    conn = op.get_bind()
    sheldon = conn.execute(
        sa.text("SELECT id FROM grade_scale WHERE code = 'sheldon'")
    ).scalar_one_or_none()
    rows = conn.execute(
        sa.text(
            "SELECT DISTINCT i.grade_id, g.numeric_value, g.is_plus, "
            "st.prefix, st.code FROM inventory_item i "
            "JOIN grade g ON g.id = i.grade_id "
            "LEFT JOIN strike_type st ON st.id = i.strike_type_id "
            "WHERE g.grade_scale_id = :s AND g.numeric_value IS NOT NULL"
        ),
        {"s": sheldon},
    ).all()
    for grade_id, number, plus, prefix, strike in rows:
        head = prefix or conn.execute(
            sa.text("SELECT grade_display(NULL, NULL, :n, false, '', true)"),
            {"n": number},
        ).scalar_one().rstrip("0123456789")
        code = f"{head}{number}{'+' if plus else ''}"
        target = conn.execute(
            sa.text("SELECT id FROM grade WHERE code = :c"), {"c": code}
        ).scalar_one_or_none()
        if target is None:
            target = conn.execute(
                sa.text(
                    "INSERT INTO grade (code, label, grade_scale_id, numeric_value, "
                    "is_plus, is_proof, sort_order, is_active, source) VALUES "
                    "(:c, :l, :s, :n, :p, :pr, 100, true, 'derived') RETURNING id"
                ),
                {
                    "c": code,
                    "l": f"{head}-{number}{'+' if plus else ''}",
                    "s": sheldon,
                    "n": number,
                    "p": plus,
                    "pr": head == "PR",
                },
            ).scalar_one()
        conn.execute(
            sa.text(
                "UPDATE inventory_item SET grade_id = :t WHERE grade_id = :g "
                "AND strike_type_id IS NOT DISTINCT FROM "
                "(SELECT id FROM strike_type WHERE code = :st)"
            ),
            {"t": target, "g": grade_id, "st": strike},
        )

    op.drop_index("ix_inventory_item_strike_type_id", table_name="inventory_item")
    op.drop_constraint(
        "inventory_item_strike_type_id_fkey", "inventory_item", type_="foreignkey"
    )
    op.drop_column("inventory_item", "strike_type_id")
    op.drop_column("grade", "grade_rank")
    op.drop_column("grade", "is_plus")
    op.drop_table("strike_type")
    op.execute("DROP FUNCTION IF EXISTS grade_display(text, text, integer, boolean, text, boolean)")

    for statement in create_views(renamed_notes=False, strike_type=False, selling=False):
        op.execute(statement)
