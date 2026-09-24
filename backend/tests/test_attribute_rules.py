"""Attributes from a note's facts: No Motto on the early $1 Silver Certificates.

docs/specs/item-attributes-design.md, section 2 and decision 5. Issue facts
are built by each test, as in test_classifier_defaults.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest
from app.attribute_rules import ALWAYS, EVIDENCE, NEVER, RULES, verdict
from app.classifier_defaults import classify, run
from app.models import (
    CurrencyDetail,
    Denomination,
    InventoryItem,
    ItemAttribute,
    ItemAttributeLink,
    NoteIssue,
    NoteType,
    ProvenanceSource,
    ReferenceMixin,
    SealColor,
)
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

ItemFactory = Callable[..., InventoryItem]
NO_MOTTO = RULES[0]


@pytest.fixture(autouse=True)
def _issues(db: Session) -> None:
    """Only the issues these tests are about: $1 Silver Certificates, a $1 FRN."""
    db.execute(delete(NoteIssue))
    for year, letter in ((1928, None), (1935, "F"), (1935, "G"), (1957, None)):
        _issue(db, "usd_note_1", year, letter, "silver_certificate", "blue")
    _issue(db, "usd_note_1", 1963, None, "frn", "green")
    _issue(db, "usd_note_5", 1934, None, "silver_certificate", "blue")
    db.flush()


def _id(db: Session, model: type[ReferenceMixin], code: str) -> int:
    return db.execute(select(model.id).where(model.code == code)).scalar_one()


def _issue(
    db: Session,
    denomination: str,
    year: int,
    letter: str | None,
    note_type: str,
    seal: str,
) -> None:
    db.add(
        NoteIssue(
            denomination_id=_id(db, Denomination, denomination),
            series_year=year,
            series_letter=letter,
            note_type_id=_id(db, NoteType, note_type),
            seal_color_id=_id(db, SealColor, seal),
        )
    )


def _note(
    db: Session,
    make_item: ItemFactory,
    year: int,
    letter: str | None = None,
    denomination: str = "usd_note_1",
    **detail: object,
) -> InventoryItem:
    item = make_item(
        kind="currency",
        title="Plain note",
        grade_id=None,
        strike_type_id=None,
        denomination_id=_id(db, Denomination, denomination),
        year_start=year,
    )
    db.add(
        CurrencyDetail(
            inventory_item_id=item.id,
            series_year=year,
            series_letter=letter,
            **detail,
        )
    )
    db.commit()
    return item


def _link(db: Session, item: InventoryItem) -> ItemAttributeLink | None:
    return db.scalar(
        select(ItemAttributeLink).where(
            ItemAttributeLink.inventory_item_id == item.id,
            ItemAttributeLink.item_attribute_id == _id(db, ItemAttribute, "no_motto"),
        )
    )


def _cases(db: Session, item: InventoryItem) -> list[tuple[str, str]]:
    return [
        (c.reason, c.detail)
        for c in classify(db).review
        if c.item_code == item.item_code
    ]


# --- the rule --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("year", "letter", "expected"),
    [
        (1928, None, ALWAYS),
        (1928, "E", ALWAYS),
        (1934, None, ALWAYS),
        (1935, "", ALWAYS),
        (1935, "f", ALWAYS),
        (1935, "G", EVIDENCE),
        (1935, "H", NEVER),
        (1957, None, NEVER),
        (1923, None, NEVER),
    ],
)
def test_no_motto_follows_the_series(
    year: int, letter: str | None, expected: str
) -> None:
    assert (
        verdict(NO_MOTTO, "silver_certificate", Decimal("1.0000"), year, letter)
        == expected
    )


@pytest.mark.parametrize(
    ("note_type", "face", "year"),
    [
        # Decision 5: the $1 run only, and Silver Certificates only.
        ("us_note", Decimal("1"), 1928),
        ("silver_certificate", Decimal("5"), 1934),
        (None, Decimal("1"), 1935),
        ("silver_certificate", None, 1935),
        ("silver_certificate", Decimal("1"), None),
    ],
)
def test_no_motto_says_nothing_outside_its_class(
    note_type: str | None, face: Decimal | None, year: int | None
) -> None:
    assert verdict(NO_MOTTO, note_type, face, year, None) is None


# --- the pass --------------------------------------------------------------------


def test_an_early_silver_certificate_gets_no_motto(
    db: Session, make_item: ItemFactory
) -> None:
    note = _note(db, make_item, 1935, "F")
    report = run(db, commit=True)

    link = _link(db, note)
    assert link is not None
    assert (link.source, link.derived_by, link.removed_at) == (
        ProvenanceSource.derived,
        "attribute_rule",
        None,
    )
    assert report.counts["attribute: no_motto added"] == 1
    # And a second run has nothing to add.
    assert not classify(db).links


def test_the_class_must_be_known_first(db: Session, make_item: ItemFactory) -> None:
    """A $5 Silver Certificate or a $1 FRN is not in the rule."""
    five = _note(db, make_item, 1934, denomination="usd_note_5")
    frn = _note(db, make_item, 1963)
    run(db, commit=True)
    assert _link(db, five) is None
    assert _link(db, frn) is None


def test_a_1935g_note_is_left_for_evidence(db: Session, make_item: ItemFactory) -> None:
    note = _note(db, make_item, 1935, "G")
    assert _cases(db, note) == [("needs evidence", "1935G: No Motto?")]
    run(db, commit=True)
    assert _link(db, note) is None


def test_a_1935g_note_rated_no_motto_needs_nothing_more(
    db: Session, make_item: ItemFactory
) -> None:
    note = _note(db, make_item, 1935, "G")
    db.add(
        ItemAttributeLink(
            inventory_item_id=note.id,
            item_attribute_id=_id(db, ItemAttribute, "no_motto"),
            source=ProvenanceSource.derived,
            derived_by="rating",
        )
    )
    db.commit()
    assert _cases(db, note) == []


def test_no_motto_on_a_later_note_is_reported_not_removed(
    db: Session, make_item: ItemFactory
) -> None:
    note = _note(db, make_item, 1957)
    db.add(
        ItemAttributeLink(
            inventory_item_id=note.id,
            item_attribute_id=_id(db, ItemAttribute, "no_motto"),
            source=ProvenanceSource.manual,
        )
    )
    db.commit()
    assert _cases(db, note) == [("disagrees", "1957 is never No Motto")]
    run(db, commit=True)
    assert _link(db, note) is not None


def test_a_removed_no_motto_stays_removed(
    db: Session,
    make_item: ItemFactory,
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    note = _note(db, make_item, 1928)
    run(db, commit=True)
    response = client.patch(
        f"/api/inventory/{note.id}", json={"attributes": []}, headers=admin_headers
    )
    assert response.status_code == 200, response.text

    run(db, commit=True)

    link = _link(db, note)
    assert link is not None and link.removed_at is not None


def test_correcting_the_series_takes_the_rules_link_back(
    db: Session,
    make_item: ItemFactory,
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    note = _note(db, make_item, 1935, "F")
    run(db, commit=True)
    assert _link(db, note) is not None

    response = client.patch(
        f"/api/inventory/{note.id}",
        json={"series_year": 1957, "series_letter": None},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    db.expire_all()
    assert _link(db, note) is None
    body = client.get(f"/api/inventory/{note.id}", headers=admin_headers).json()
    assert body["attributes"] == []


def test_a_new_early_note_gets_no_motto_when_saved(
    db: Session,
    make_item: ItemFactory,
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    note = _note(db, make_item, 1957)
    run(db, commit=True)
    response = client.patch(
        f"/api/inventory/{note.id}",
        json={"series_year": 1928},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    body = client.get(f"/api/inventory/{note.id}", headers=admin_headers).json()
    assert [(a["code"], a["derived_by"]) for a in body["attributes"]] == [
        ("no_motto", "attribute_rule")
    ]


def test_a_link_someone_else_made_is_never_taken_back(
    db: Session, make_item: ItemFactory
) -> None:
    """The rating said No Motto on a 1935G; the rule's EVIDENCE leaves it."""
    note = _note(db, make_item, 1935, "G")
    db.add(
        ItemAttributeLink(
            inventory_item_id=note.id,
            item_attribute_id=_id(db, ItemAttribute, "no_motto"),
            source=ProvenanceSource.derived,
            derived_by="rating",
        )
    )
    db.commit()
    run(db, commit=True)
    link = _link(db, note)
    assert link is not None and link.removed_at is None


def test_a_class_taken_back_takes_no_motto_with_it(
    db: Session,
    make_item: ItemFactory,
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    """1935E is not in these facts: the class is no longer known, nor No Motto."""
    note = _note(db, make_item, 1935, "F")
    run(db, commit=True)

    response = client.patch(
        f"/api/inventory/{note.id}",
        json={"series_letter": "E"},
        headers=admin_headers,
    )

    assert response.status_code == 200, response.text
    body = client.get(f"/api/inventory/{note.id}", headers=admin_headers).json()
    assert body["note_type"] is None
    assert body["attributes"] == []
