"""Fancy-serial designations derived from the serial number.

These are what a PMG or PCGS submission asks the sender to declare, and they
carry real premiums, so the risk runs both ways: a missed designation sells a
note too cheaply, and a wrong one is a claim the note cannot support.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.models import CurrencyDetail, InventoryItem, ItemAttribute, ItemAttributeLink
from app.serial_patterns import analyse, is_incomplete, run
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.builders import ItemFactory

#: A star and nothing else: no ladder, repeater or fancy digits.
STAR_SERIAL = "A38164927*"


@pytest.mark.parametrize(
    ("serial", "expected"),
    [
        ("A12344321B", {"radar", "fancy_serial"}),
        ("A12341234B", {"repeater", "fancy_serial"}),
        ("A11112222B", {"binary", "double_quad", "fancy_serial"}),
        ("M88888888", {"solid_serial", "radar", "repeater", "fancy_serial"}),
        ("A12345678B", {"ladder", "fancy_serial"}),
        # Low and high are positional -- where the note sits in the print
        # run -- so they stack with the pattern designations rather than
        # replacing them.
        ("A00001234B", {"low_serial"}),
        ("A98765432B", {"high_serial", "ladder", "fancy_serial"}),
        (
            "A99999999B",
            {
                "high_serial",
                "solid_serial",
                "radar",
                "repeater",
                "fancy_serial",
            },
        ),
        ("A04301989B", {"birthday", "fancy_serial"}),
        ("A15141514B", {"trinary", "repeater", "fancy_serial"}),
        ("A25282252B", {"trinary", "fancy_serial"}),
        # Four of one digit then four of another. Binary too -- two distinct
        # digits -- but the arrangement is the thing collectors pay for.
        ("E00003333B", {"double_quad", "binary", "low_serial", "fancy_serial"}),
        ("A19472856B", set()),
    ],
)
def test_designations(serial: str, expected: set[str]) -> None:
    assert analyse(serial) == expected


def test_a_star_is_not_a_fancy_serial() -> None:
    """Two different questions on a grading form, and two different suffixes.

    A star swept into `fancy_serial` would be a claim about the note's digits
    that its digits do not support.
    """
    plain_star = analyse("A19472856*")
    assert plain_star == {"star"}
    assert "fancy_serial" not in plain_star

    # A star note whose digits *are* patterned earns both, separately.
    both = analyse("A12344321*")
    assert {"star", "radar", "fancy_serial"} <= both


def test_short_serials_yield_no_pattern() -> None:
    """The eight-digit rule.

    "59" has two distinct digits, so a naive binary test calls it a binary
    note. It is a truncated field, not a note, and reading patterns from a
    short serial would invent designations.
    """
    for serial in ("59", "887", "1791040", "99V7711"):
        assert is_incomplete(serial)
        assert analyse(serial) - {"star"} == set()


def test_a_short_serial_still_shows_its_star() -> None:
    """The asterisk is not a digit pattern, so truncation does not hide it."""
    assert analyse("*59") == {"star"}


def test_consecutive_is_never_derived() -> None:
    """It describes a run of notes, which one serial cannot show.

    It is recorded by a person who knows they bought a run. No single serial
    could establish that, so this must not try.
    """
    for serial in ("A12345678B", "A00000001B", "M88888888"):
        assert "consecutive" not in analyse(serial)


def test_empty_serial_is_silent() -> None:
    assert analyse("") == set()


def test_an_internal_letter_warns_rather_than_refusing() -> None:
    """Usually a transposition, but not always.

    S97535476A typed as S9753547A6 is the common case. But a note can carry
    an internal letter and be correct as recorded, so refusing outright would
    block a legitimate serial. It warns instead.
    """
    from app.serial_patterns import check

    issues = check("S9753547A6")
    # Two: the internal letter, then the advisory ceiling -- its eight digits
    # read 97,535,476, above 96,000,000.
    assert [i.severity for i in issues] == ["warning", "warning"]
    assert "one position early" in issues[0].message


def test_short_and_high_serials_only_warn() -> None:
    """Unusual is not impossible: a short or a high serial is a warning."""
    from app.serial_patterns import check

    short = check("E9801342C")
    assert [i.severity for i in short] == ["warning"]
    assert "only 7 digits" in short[0].message

    high = check("A99889530B", 2001)
    assert [i.severity for i in high] == ["warning"]
    assert "worth confirming" in high[0].message


def test_the_advisory_ceiling_itself_raises_nothing() -> None:
    """96,000,000 exactly is not above the ceiling; an ordinary value is clean.

    Above 96 million is advisory; no eight-digit serial exceeds 99,999,999.

    The distinction matters: the print-run limit varies by series and this
    project has no sourced table of them, so the wording must not claim more
    than is known.
    """
    from app.serial_patterns import check

    assert check("A96000000B") == []
    assert check("A12345678B") == []


def test_ends_may_carry_letters_or_a_star() -> None:
    from app.serial_patterns import check

    for serial in ("A12345678B", "AB12345678C", "*12345678B", "A12345678*"):
        assert check(serial) == [], serial


def test_shape_rules_apply_to_us_notes_only() -> None:
    """A world note is not malformed for looking unlike a US one.

    Many national formats interleave letters and digits legitimately, so
    applying the US shape to a Bank of Canada or Bundesbank note would fault
    a correct serial.
    """
    from app.serial_patterns import check

    assert check("S9753547A6", country="US")[0].severity == "warning"
    assert check("S9753547A6", country="CA") == []
    assert check("AB-1234/567", country="DE") == []


def test_pre_1928_notes_are_not_shape_checked() -> None:
    """Large-size and obsolete issues predate the convention.

    An 1852 $5 can carry the serial S143r1 and be correct. The
    letters-at-the-ends rule arrived with small-size notes in 1928, so
    applying it 76 years early would condemn a genuine note.
    """
    from app.serial_patterns import check

    assert check("S143r1", 1852) == []
    assert check("S143r1", 1957) != []


# ---------------------------------------------------------------------------
# The pass over the collection
# ---------------------------------------------------------------------------


def _note(db: Session, make_item: ItemFactory, serial: str) -> InventoryItem:
    """A bought banknote with this serial number, its words naming no attribute."""
    note = make_item(
        kind="currency",
        description="plain",
        grade_id=None,
        strike_type_id=None,
        item_cost=Decimal("5.00"),
    )
    db.add(CurrencyDetail(inventory_item_id=note.id, serial_number=serial))
    db.commit()
    return note


def _links(db: Session, item: InventoryItem) -> list[tuple[str, str | None]]:
    """The item's attribute links in force: code, and the rule that added it."""
    return sorted(
        db.execute(
            select(ItemAttribute.code, ItemAttributeLink.derived_by)
            .join(
                ItemAttribute, ItemAttribute.id == ItemAttributeLink.item_attribute_id
            )
            .where(
                ItemAttributeLink.inventory_item_id == item.id,
                ItemAttributeLink.removed_at.is_(None),
            )
        ).tuples()
    )


def test_a_retired_designation_is_not_added(
    db: Session, make_item: ItemFactory
) -> None:
    """Retiring a value stops every new use of it, the pass's included."""
    note = _note(db, make_item, STAR_SERIAL)
    star = db.scalars(select(ItemAttribute).where(ItemAttribute.code == "star")).one()
    star.is_active = False
    db.commit()

    stats, _ = run(db, commit=True)

    assert _links(db, note) == []
    assert stats["retired_attribute:star"] == 1


def test_a_retired_designation_the_pass_added_is_kept(
    db: Session, make_item: ItemFactory
) -> None:
    """Retiring leaves every record that uses the value as it is."""
    note = _note(db, make_item, STAR_SERIAL)
    run(db, commit=True)
    star = db.scalars(select(ItemAttribute).where(ItemAttribute.code == "star")).one()
    star.is_active = False
    db.commit()

    run(db, commit=True)

    assert _links(db, note) == [("star", "serial_pattern")]


def test_links_merged_into_another_attribute_survive_the_next_pass(
    db: Session,
    make_item: ItemFactory,
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    """A merge moves the pass's links to a value the pass does not derive.

    They are then the merge's, not the pass's: a pass that read them as its
    own would take them back as designations the serial no longer earns.
    """
    note = _note(db, make_item, STAR_SERIAL)
    run(db, commit=True)
    made = client.post(
        "/api/reference/item_attribute",
        json={
            "code": "replacement_test",
            "label": "Replacement Test",
            "extra": {"applies_to": "currency", "attribute_group": "serial"},
        },
        headers=admin_headers,
    )
    assert made.status_code == 201, made.text
    merged = client.post(
        "/api/reference/item_attribute/star/merge",
        json={"into": "replacement_test"},
        headers=admin_headers,
    )
    assert merged.status_code == 200, merged.text

    run(db, commit=True)

    assert _links(db, note) == [("replacement_test", None)]


def test_links_merged_into_a_designation_the_pass_derives_stay_its_own(
    db: Session,
    make_item: ItemFactory,
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    """Into a code the pass derives, the link is still the pass's to judge."""
    note = _note(db, make_item, "A12344321B")
    run(db, commit=True)
    assert ("radar", "serial_pattern") in _links(db, note)
    merged = client.post(
        "/api/reference/item_attribute/radar/merge",
        json={"into": "repeater"},
        headers=admin_headers,
    )
    assert merged.status_code == 200, merged.text

    assert ("repeater", "serial_pattern") in _links(db, note)
