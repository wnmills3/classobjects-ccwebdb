"""Other names for classifier values: lookup, search, import and the console.

docs/specs/item-attributes-design.md, section 1. The seeded note-class and
strike-type aliases are read rather than invented, so a mistake in the seed
files shows up here too.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import pytest
from app import aliases
from app.classifier_defaults import load_facts
from app.importers.engine import ImportReport
from app.importers.loader import SchemaLoader
from app.inventory_search import COIN_VIEW, CURRENCY_VIEW, ViewSpec, search
from app.models import (
    CoinDetail,
    CurrencyDetail,
    GradeDesignation,
    InventoryItem,
    ItemAttribute,
    ItemAttributeLink,
    Mint,
    NoteType,
    ProvenanceSource,
    ReferenceAlias,
    ReferenceMixin,
    Series,
    SeriesAlias,
    StrikeType,
)
from app.seeding import _seed_reference_aliases, _seed_series_aliases
from app.series_match import build_rules, match
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

ItemFactory = Callable[..., InventoryItem]


def _id(db: Session, model: type[ReferenceMixin], code: str) -> int:
    return db.execute(select(model.id).where(model.code == code)).scalar_one()


def _codes(db: Session, model: type[ReferenceMixin], ids: list[int]) -> set[str]:
    return set(db.scalars(select(model.code).where(model.id.in_(ids))))


def _alias_row(db: Session, table: str, alias: str) -> ReferenceAlias:
    return db.execute(
        select(ReferenceAlias).where(
            ReferenceAlias.table_name == table, ReferenceAlias.alias == alias
        )
    ).scalar_one()


# --- resolve ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("word", "code", "by"),
    [
        ("us_note", "us_note", "code"),
        ("US_NOTE", "us_note", "code"),
        ("united states note", "us_note", "label"),
        ("Legal Tender", "us_note", "alias"),
        ("  legal   tender ", "us_note", "alias"),
    ],
)
def test_a_word_resolves_by_code_then_label_then_alias(
    db: Session, word: str, code: str, by: str
) -> None:
    found = aliases.resolve(db, NoteType, word)
    assert found is not None
    assert (db.get_one(NoteType, found.row_id).code, found.by) == (code, by)


def test_an_alias_two_values_share_resolves_to_neither(db: Session) -> None:
    """Two note classes are called National Currency; guessing would be silent."""
    assert aliases.ids_named(db, NoteType, "National Currency")
    assert aliases.resolve(db, NoteType, "National Currency") is None


def test_nothing_resolves_nothing(db: Session) -> None:
    assert aliases.resolve(db, NoteType, "Confederate") is None
    assert aliases.resolve(db, NoteType, "  ") is None


def test_a_retired_alias_no_longer_names_anything(db: Session) -> None:
    national = _id(db, NoteType, "national_bank_note")
    assert aliases.resolve(db, NoteType, "Natl") == aliases.Resolved(national, "alias")

    _alias_row(db, "note_type", "Natl").is_active = False
    db.flush()
    assert aliases.resolve(db, NoteType, "Natl") is None
    assert aliases.ids_named(db, NoteType, "Natl") == []
    assert "Natl" not in aliases.aliases_by_row(db, NoteType)[national]
    assert (
        "Natl" in aliases.aliases_by_row(db, NoteType, include_retired=True)[national]
    )


# --- ids_named ------------------------------------------------------------------


def test_short_text_must_be_a_whole_name(db: Session) -> None:
    """PR is the Proof strike, and s is not every strike containing an s."""
    assert _codes(db, StrikeType, aliases.ids_named(db, StrikeType, "pr")) == {"proof"}
    assert aliases.ids_named(db, StrikeType, "s") == []
    assert _codes(db, Mint, aliases.ids_named(db, Mint, "d")) == {"D"}


def test_longer_text_matches_part_of_a_name(db: Session) -> None:
    assert _codes(db, StrikeType, aliases.ids_named(db, StrikeType, "rev")) == {
        "reverse_proof",
        "enhanced_reverse_proof",
    }
    assert _codes(db, NoteType, aliases.ids_named(db, NoteType, "tender")) == {
        "us_note"
    }


# --- add and remove --------------------------------------------------------------


def test_an_added_alias_is_manual_and_works_at_once(db: Session) -> None:
    dcam = _id(db, GradeDesignation, "DCAM")
    created = aliases.add_alias(db, GradeDesignation, dcam, " Black  Cameo ")

    assert (created.alias, created.source) == ("Black Cameo", ProvenanceSource.manual)
    assert aliases.resolve(db, GradeDesignation, "black cameo") == aliases.Resolved(
        dcam, "alias"
    )


@pytest.mark.parametrize(
    ("alias", "reason"),
    [
        ("", "needs some text"),
        ("x" * 65, "at most 64"),
        ("dcam", "own name"),
        ("DCAM (Deep Cameo)", "own name"),
        ("CAM", "name of CAM (Cameo)"),
        ("pl (prooflike)", "name of PL (Prooflike)"),
    ],
)
def test_an_alias_that_could_never_be_reached_is_refused(
    db: Session, alias: str, reason: str
) -> None:
    with pytest.raises(aliases.AliasError, match=re.escape(reason)):
        aliases.add_alias(
            db, GradeDesignation, _id(db, GradeDesignation, "DCAM"), alias
        )


def test_the_same_alias_twice_on_one_value_is_refused(db: Session) -> None:
    us_note = _id(db, NoteType, "us_note")
    with pytest.raises(aliases.AliasError, match="already an alias"):
        aliases.add_alias(db, NoteType, us_note, "legal tender")


def test_two_values_may_share_an_alias(db: Session) -> None:
    """Search finds both; the importer, which cannot choose, uses neither."""
    silver = _id(db, NoteType, "silver_certificate")
    gold = _id(db, NoteType, "gold_certificate")
    aliases.add_alias(db, NoteType, silver, "Certificate")
    aliases.add_alias(db, NoteType, gold, "Certificate")

    assert set(aliases.ids_named(db, NoteType, "Certificate")) >= {silver, gold}
    assert aliases.resolve(db, NoteType, "Certificate") is None


def test_removing_an_added_alias_deletes_it(db: Session) -> None:
    dcam = _id(db, GradeDesignation, "DCAM")
    aliases.add_alias(db, GradeDesignation, dcam, "Black Cameo")
    aliases.remove_alias(db, GradeDesignation, dcam, "black cameo")

    assert (
        db.scalar(select(ReferenceAlias).where(ReferenceAlias.alias == "Black Cameo"))
        is None
    )


def test_removing_a_shipped_alias_retires_it_and_a_seed_load_keeps_it_retired(
    db: Session,
) -> None:
    us_note = _id(db, NoteType, "us_note")
    aliases.remove_alias(db, NoteType, us_note, "Legal Tender")

    shipped = [{"table": "note_type", "code": "us_note", "alias": "Legal Tender"}]
    stats = _seed_reference_aliases(db, {"reference_alias": shipped})

    assert stats == {"unchanged": 1}
    assert _alias_row(db, "note_type", "Legal Tender").is_active is False
    assert aliases.resolve(db, NoteType, "Legal Tender") is None


def test_adding_a_retired_alias_back_restores_the_shipped_row(db: Session) -> None:
    us_note = _id(db, NoteType, "us_note")
    aliases.remove_alias(db, NoteType, us_note, "Legal Tender")
    restored = aliases.add_alias(db, NoteType, us_note, "legal tender")

    assert restored is _alias_row(db, "note_type", "Legal Tender")
    assert (restored.is_active, restored.source) == (True, ProvenanceSource.seeded)


def test_removing_what_is_not_an_alias_is_refused(db: Session) -> None:
    us_note = _id(db, NoteType, "us_note")
    with pytest.raises(aliases.AliasError, match="not an alias"):
        aliases.remove_alias(db, NoteType, us_note, "Greenback")
    aliases.remove_alias(db, NoteType, us_note, "Legal Tender")
    with pytest.raises(aliases.AliasError, match="not an alias"):
        aliases.remove_alias(db, NoteType, us_note, "Legal Tender")


def test_series_aliases_retire_the_same_way(db: Session) -> None:
    walker = _id(db, Series, "walking_liberty_half")
    assert match("1943 Walker", None, build_rules(db)) == {"walking_liberty_half"}
    aliases.remove_alias(db, Series, walker, "Walker")

    shipped = [{"series": "walking_liberty_half", "alias": "Walker"}]
    _seed_series_aliases(db, {"series_alias": shipped})
    row = db.execute(
        select(SeriesAlias).where(SeriesAlias.alias == "Walker")
    ).scalar_one()

    assert row.is_active is False
    assert aliases.ids_named(db, Series, "Walker") == []
    assert not match("1943 Walker", None, build_rules(db))


# --- the other readers ------------------------------------------------------------


def test_the_defaults_pass_ignores_a_retired_note_class_alias(db: Session) -> None:
    us_note = _id(db, NoteType, "us_note")

    def reads(text: str) -> bool:
        return any(
            type_id == us_note and pattern.search(text)
            for type_id, pattern in load_facts(db).class_names
        )

    assert reads("1917 $1 Legal Tender")
    aliases.remove_alias(db, NoteType, us_note, "Legal Tender")
    aliases.remove_alias(db, NoteType, us_note, "Legal Tender Note")
    assert not reads("1917 $1 Legal Tender")


# --- search ----------------------------------------------------------------------


def _codes_found(db: Session, view: ViewSpec, query: str) -> set[str]:
    rows, _ = search(db, view, params={}, query=query)
    return {row["item_code"] for row in rows}


def test_search_finds_a_coin_by_its_strike_designation_or_mint(
    db: Session, make_item: ItemFactory
) -> None:
    proof = make_item(
        description="plain", grade_id=None, strike_type_id=_id(db, StrikeType, "proof")
    )
    cameo = make_item(
        description="plain",
        grade_designation_id=_id(db, GradeDesignation, "DCAM"),
    )
    denver = make_item(description="plain")
    db.add(CoinDetail(inventory_item_id=denver.id, mint_id=_id(db, Mint, "D")))
    db.commit()
    aliases.add_alias(
        db, GradeDesignation, _id(db, GradeDesignation, "DCAM"), "Black Cameo"
    )
    db.commit()

    assert proof.item_code in _codes_found(db, COIN_VIEW, "PR")
    assert proof.item_code in _codes_found(db, COIN_VIEW, "proof")
    assert cameo.item_code in _codes_found(db, COIN_VIEW, "black cameo")
    assert cameo.item_code in _codes_found(db, COIN_VIEW, "deep cameo")
    assert denver.item_code in _codes_found(db, COIN_VIEW, "Denver")
    assert _codes_found(db, COIN_VIEW, "Denver") == {denver.item_code}


def test_search_finds_a_note_by_its_class_alias_or_serial_feature(
    db: Session, make_item: ItemFactory
) -> None:
    note = make_item(
        kind="currency", description="plain", grade_id=None, strike_type_id=None
    )
    db.add(
        CurrencyDetail(
            inventory_item_id=note.id, note_type_id=_id(db, NoteType, "us_note")
        )
    )
    starred = make_item(
        kind="currency", description="plain", grade_id=None, strike_type_id=None
    )
    db.add(
        ItemAttributeLink(
            inventory_item_id=starred.id,
            item_attribute_id=_id(db, ItemAttribute, "star"),
        )
    )
    db.commit()

    assert _codes_found(db, CURRENCY_VIEW, "legal tender") == {note.item_code}
    assert _codes_found(db, CURRENCY_VIEW, "star note") == {starred.item_code}
    # A coin view never looks at note classes.
    assert note.item_code not in _codes_found(db, COIN_VIEW, "legal tender")


def test_a_note_matching_two_ways_is_listed_once(
    db: Session, make_item: ItemFactory
) -> None:
    note = make_item(
        kind="currency",
        description="Legal Tender Star",
        grade_id=None,
        strike_type_id=None,
    )
    db.add(
        CurrencyDetail(
            inventory_item_id=note.id, note_type_id=_id(db, NoteType, "us_note")
        )
    )
    db.add(
        ItemAttributeLink(
            inventory_item_id=note.id,
            item_attribute_id=_id(db, ItemAttribute, "star"),
        )
    )
    db.commit()
    rows, total = search(db, CURRENCY_VIEW, params={}, query="star")
    assert (total, [r["item_code"] for r in rows]) == (1, [note.item_code])


# --- import ------------------------------------------------------------------------


def test_the_importer_reads_an_alias_and_counts_every_row(db: Session) -> None:
    loader = SchemaLoader(db)
    us_note = _id(db, NoteType, "us_note")

    assert loader.by_label(NoteType, "Legal Tender") == us_note
    assert loader.by_label(NoteType, "Legal Tender") == us_note
    assert loader.aliased == {("note_type", "Legal Tender", "us_note"): 2}
    assert loader.derived == {}


def test_the_importer_uses_a_label_without_calling_it_an_alias(db: Session) -> None:
    loader = SchemaLoader(db)
    assert loader.by_label(StrikeType, "Business Strike") == _id(
        db, StrikeType, "business"
    )
    assert loader.aliased == {}
    assert loader.derived == {}


def test_the_importer_invents_a_row_rather_than_guess_a_shared_alias(
    db: Session,
) -> None:
    loader = SchemaLoader(db)
    found = loader.by_label(NoteType, "National Currency")

    assert found not in {
        _id(db, NoteType, "frbn"),
        _id(db, NoteType, "national_bank_note"),
    }
    assert loader.derived == {"note_type": 1}
    assert loader.aliased == {}


def test_the_report_names_each_aliased_value() -> None:
    report = ImportReport(
        source_path="x.xlsx",
        source_kind="xlsx",
        sha256="0" * 64,
        profile_name="p",
        mode="commit",
        aliased_reference_values={"note_type: Legal Tender -> us_note": 3},
    )
    assert (
        "aliased  : note_type: Legal Tender -> us_note  (3 rows)"
        in report._summary_lines()
    )


# --- the API -------------------------------------------------------------------------


def _value(body: dict, code: str) -> dict:
    return next(v for v in body["values"] if v["code"] == code)


def test_a_vocabulary_lists_each_values_aliases(client: TestClient) -> None:
    body = client.get("/api/reference/note_type").json()
    assert _value(body, "us_note")["aliases"] == ["Legal Tender", "Legal Tender Note"]
    assert _value(body, "us_note")["retired_aliases"] == []


def test_the_console_adds_and_removes_an_alias(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    url = "/api/reference/grade_designation/DCAM/aliases"
    added = client.post(url, json={"alias": "Black Cameo"}, headers=admin_headers)
    assert added.status_code == 201
    assert added.json()["aliases"] == ["Black Cameo", "UC", "UCAM", "Ultra Cameo"]

    again = client.post(url, json={"alias": "black cameo"}, headers=admin_headers)
    assert again.status_code == 409

    removed = client.delete(url, params={"alias": "Black Cameo"}, headers=admin_headers)
    assert removed.status_code == 200
    assert removed.json()["aliases"] == ["UC", "UCAM", "Ultra Cameo"]
    # Added here, so deleted rather than kept as retired.
    assert removed.json()["retired_aliases"] == []

    missing = client.delete(url, params={"alias": "Black Cameo"}, headers=admin_headers)
    assert missing.status_code == 404


def test_a_removed_shipped_alias_is_listed_as_retired_for_editing(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    url = "/api/reference/note_type/us_note/aliases"
    removed = client.delete(
        url, params={"alias": "Legal Tender"}, headers=admin_headers
    ).json()
    assert removed["aliases"] == ["Legal Tender Note"]
    assert removed["retired_aliases"] == ["Legal Tender"]

    public = client.get("/api/reference/note_type").json()
    assert _value(public, "us_note")["retired_aliases"] == []
    editing = client.get(
        "/api/reference/note_type", params={"include_inactive": True}
    ).json()
    assert _value(editing, "us_note")["retired_aliases"] == ["Legal Tender"]


def test_a_retired_value_says_so_when_listed_for_editing(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    client.patch(
        "/api/reference/note_type/us_note",
        json={"label": "United States Note", "is_active": False},
        headers=admin_headers,
    )
    editing = client.get(
        "/api/reference/note_type", params={"include_inactive": True}
    ).json()
    assert _value(editing, "us_note")["is_active"] is False
    assert _value(editing, "frn")["is_active"] is True


def test_only_staff_edit_aliases(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    url = "/api/reference/note_type/us_note/aliases"
    assert client.post(url, json={"alias": "Greenback"}).status_code == 401
    assert (
        client.post(
            url, json={"alias": "Greenback"}, headers=customer_headers
        ).status_code
        == 403
    )
    assert (
        client.delete(
            url, params={"alias": "Legal Tender"}, headers=customer_headers
        ).status_code
        == 403
    )


def test_an_alias_for_an_unknown_value_is_404(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    for url in (
        "/api/reference/note_type/no_such/aliases",
        "/api/reference/no_such_table/x/aliases",
    ):
        response = client.post(url, json={"alias": "x"}, headers=admin_headers)
        assert response.status_code == 404


@pytest.mark.parametrize(
    ("word", "code"),
    [("UCAM", "DCAM"), ("ultra cameo", "DCAM"), ("UC", "DCAM"), ("dpl", "DMPL")],
)
def test_the_services_equivalent_designations_are_aliases(
    db: Session, word: str, code: str
) -> None:
    """NGC's Ultra Cameo is Deep Cameo and its DPL is DMPL: aliases, not rows."""
    found = aliases.resolve(db, GradeDesignation, word)
    assert found is not None
    assert db.get_one(GradeDesignation, found.row_id).code == code


def test_the_newer_designations_are_rows(db: Session) -> None:
    for code in ("FT", "5FS", "6FS"):
        assert _id(db, GradeDesignation, code)
