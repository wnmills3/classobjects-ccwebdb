"""The classifier vocabularies, as dropdowns consume them."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from app.models import REFERENCE_MODELS, Grade, ProvenanceSource, ReferenceMixin
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def test_the_available_vocabularies_are_listed(client: TestClient) -> None:
    tables = client.get("/api/reference").json()
    assert "grade" in tables
    assert "item_kind" in tables
    assert "denomination" in tables


def test_a_vocabulary_comes_back_ready_for_a_picker(client: TestClient) -> None:
    body = client.get("/api/reference/item_kind").json()
    codes = [v["code"] for v in body["values"]]

    assert body["table"] == "item_kind"
    assert "coin" in codes
    assert "bullion" in codes
    # Ordered by sort_order, so the picker shows them in a sensible sequence
    # rather than alphabetically or by insertion.
    orders = [v["sort_order"] for v in body["values"]]
    assert orders == sorted(orders)


def test_a_vocabulary_says_whether_its_order_means_anything(
    client: TestClient,
) -> None:
    """Grades are in scale order; series are alphabetical.

    The Vocabularies page offers to change a value's position only where the
    position is what the pickers sort by.
    """
    assert client.get("/api/reference/grade").json()["sequenced"] is True
    assert client.get("/api/reference/series").json()["sequenced"] is False


def test_a_position_beyond_the_column_is_refused_not_a_500(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.patch(
        "/api/reference/grade/64",
        headers=admin_headers,
        json={"label": "64", "sort_order": 2**31},
    )
    assert response.status_code == 422, response.text


def test_the_endpoint_is_public(client: TestClient) -> None:
    """Vocabularies are public; they reveal nothing held.

    The catalog's own filters need it, and a vocabulary is not data:
    knowing MS64 exists reveals nothing about what anyone owns.
    """
    assert client.get("/api/reference/grade").status_code == 200


def test_table_specific_columns_come_through(client: TestClient) -> None:
    """Table-specific columns travel in `extra`.

    A denomination's face value and an error type's applies_to are what
    make those pickers usable, and they differ per table -- so they arrive in
    `extra` rather than forcing a response model per table.
    """
    denominations = client.get("/api/reference/denomination").json()["values"]
    dollar = next(v for v in denominations if v["code"] == "usd_coin_1_00")
    assert dollar["extra"]["face_value"] == "1.0000"
    assert dollar["extra"]["kind"] == "coin"

    errors = client.get("/api/reference/error_type").json()["values"]
    doubled = next(v for v in errors if v["code"] == "doubled_die")
    assert doubled["extra"]["applies_to"] == "coin"


def test_foreign_keys_come_through_as_codes_not_ids(client: TestClient) -> None:
    """Foreign keys inside a vocabulary resolve to codes.

    The same rule the rest of the API follows: a client never has to know
    an id, because ids differ between installations.
    """
    grades = client.get("/api/reference/grade").json()["values"]
    ms65 = next(v for v in grades if v["code"] == "65")

    assert ms65["extra"]["grade_scale"] == "sheldon"
    assert "grade_scale_id" not in ms65["extra"]


def test_grades_carry_the_number_that_makes_them_sortable(
    client: TestClient,
) -> None:
    grades = client.get("/api/reference/grade").json()["values"]
    ms65 = next(v for v in grades if v["code"] == "65")
    circulated = next(v for v in grades if v["code"] == "CIRC")

    assert ms65["extra"]["numeric_value"] == 65
    # A grade with no number carries none rather than a fictional one.
    assert "numeric_value" not in circulated["extra"]


def test_provenance_is_visible(client: TestClient, db: Session) -> None:
    """Provenance is visible on every value.

    A picker can distinguish shipped vocabulary from values derived locally,
    which is the same distinction that gates exporting.
    """
    db.add(Grade(code="LOCAL_X", label="Local", source=ProvenanceSource.derived))
    db.commit()

    values = client.get("/api/reference/grade").json()["values"]
    local = next(v for v in values if v["code"] == "LOCAL_X")
    seeded = next(v for v in values if v["code"] == "65")

    assert local["source"] == "derived"
    assert seeded["source"] == "seeded"


def test_retired_values_are_hidden_but_reachable(
    client: TestClient, db: Session
) -> None:
    """Retired values are hidden but still fetchable.

    An old record may still reference a classifier that should not be
    offered for new ones, and the form still has to render it.
    """
    db.add(
        Grade(
            code="RETIRED_X",
            label="Retired",
            is_active=False,
            source=ProvenanceSource.manual,
        )
    )
    db.commit()

    visible = {v["code"] for v in client.get("/api/reference/grade").json()["values"]}
    assert "RETIRED_X" not in visible

    everything = client.get("/api/reference/grade?include_inactive=true").json()
    assert "RETIRED_X" in {v["code"] for v in everything["values"]}


def test_an_unknown_vocabulary_is_a_404(client: TestClient) -> None:
    response = client.get("/api/reference/not_a_table")
    assert response.status_code == 404
    assert "not_a_table" in response.json()["detail"]


def test_every_listed_vocabulary_can_actually_be_fetched(
    client: TestClient,
) -> None:
    """Guards against the index and the handler drifting apart."""
    for table in client.get("/api/reference").json():
        response = client.get(f"/api/reference/{table}")
        assert response.status_code == 200, f"{table} listed but not fetchable"
        assert response.json()["table"] == table


def _classifier_tables() -> dict[str, type[ReferenceMixin]]:
    """Every concrete `ReferenceMixin` subclass, by table name.

    Walked from the mixin rather than from a list, because a list is exactly
    what the test below is checking. Subclasses without a `__tablename__` are
    abstract intermediates, not tables.
    """
    found: dict[str, type[ReferenceMixin]] = {}
    pending = list(ReferenceMixin.__subclasses__())
    while pending:
        model = pending.pop()
        pending.extend(model.__subclasses__())
        table = getattr(model, "__tablename__", None)
        if isinstance(table, str):
            found[table] = model
    return found


def test_every_reference_table_is_registered() -> None:
    """A classifier table missing from `REFERENCE_MODELS` has no endpoint.

    `routers.reference.TABLES` is built from that tuple alone, so a
    `ReferenceMixin` subclass left out of it answers 404 at
    `/api/reference/<table>` -- and the console's `useReference` swallows the
    failure and caches an empty vocabulary, so the form renders with the
    picker silently missing rather than with an error anyone would notice.
    That is exactly what happened to `sales_fee_kind`: the model, the
    migration, the seeded codes and the dialog all shipped, and the tuple was
    never touched, so **Record sale...** offered no fee rows at all.

    Nothing else in the suite compares the two, which is why the omission
    survived a whole branch.
    """
    registered = {model.__tablename__ for model in REFERENCE_MODELS}
    missing = sorted(set(_classifier_tables()) - registered)
    assert missing == [], (
        f"classifier table(s) {missing} are ReferenceMixin subclasses with no "
        "entry in models.REFERENCE_MODELS, so /api/reference/<table> answers "
        "404 for them"
    )


def test_the_fee_vocabulary_reaches_a_picker(client: TestClient) -> None:
    """The six seeded fee kinds come back in the migration's curated order.

    `RecordSaleDialog` builds one fee row per value this returns. A 404 here
    is a dialog whose fees are all zero and whose net always equals its
    gross, with nothing on screen saying so.

    The order asserted is the migration's `sort_order`, not alphabetical:
    `sales_fee_kind` is in `_SEQUENCED_TABLES` because the sequence is the
    order a platform's statement reads in and it puts the catch-all "other"
    last -- alphabetical puts "Other" third, and
    `docs/system-administration.md` prints the curated order in writing.
    This assertion would have passed under either sort before that change,
    because by code and by label the six happen to agree; the curated order
    differs from both, so it now pins something.
    """
    response = client.get("/api/reference/sales_fee_kind")
    assert response.status_code == 200
    body = response.json()
    assert body["table"] == "sales_fee_kind"
    assert [value["code"] for value in body["values"]] == [
        "commission",
        "processing",
        "listing",
        "shipping_label",
        "promotion",
        "other",
    ]


def test_the_catch_all_fee_kind_comes_last(client: TestClient) -> None:
    """The catch-all "other" last is the point of the curated order.

    The list above would also pass if the whole sequence were reversed by
    accident; this one names the property the ruling actually rests on, and
    fails on its own if `sales_fee_kind` ever drops out of
    `_SEQUENCED_TABLES` (alphabetical puts "Other" third).
    """
    values = client.get("/api/reference/sales_fee_kind").json()["values"]
    assert values[-1]["code"] == "other"


def test_a_fee_kind_cannot_be_retired(client: TestClient) -> None:
    """Retiring one would 422 every sale that charges it.

    `record_sale` resolves a fee line's kind through `require_code`, which
    filters on `is_active` -- so a retired `commission` makes every sale
    charging a commission fail, naming a code the dialog had just offered.
    """
    values = client.get("/api/reference/sales_fee_kind").json()["values"]
    commission = next(value for value in values if value["code"] == "commission")
    assert commission["retirable"] is False


# ---------------------------------------------------------------------------
# Picker order: alphabetical, except where the order is the meaning
# ---------------------------------------------------------------------------


def test_a_descriptive_vocabulary_comes_back_alphabetically(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Attributes are scanned by name; their seeded sort_order groups them."""
    labels = [
        value["label"]
        for value in client.get("/api/reference/item_attribute").json()["values"]
    ]

    assert labels == sorted(labels, key=str.lower)


def test_grades_keep_their_scale_order(client: TestClient) -> None:
    """70, 69+, 69 ... is the scale's own order; alphabetical would be noise."""
    codes = [v["code"] for v in client.get("/api/reference/grade").json()["values"]]

    assert codes.index("70") < codes.index("65") < codes.index("50")


def test_denominations_ascend_by_face_value_coins_then_notes(
    client: TestClient,
) -> None:
    """A denomination is a value on a scale, like a grade.

    Face value ascends within coins, then within notes, and every coin
    precedes every note -- once a picker filters by kind (bullion or
    banknote), the values it is left with still read low to high.
    Alphabetical would open with "$1 Bill" ahead of "Cent". Within one
    currency: a 5 peso note and a $1000 bill are on different scales.
    """
    values = client.get("/api/reference/denomination").json()["values"]
    for kind in ("coin", "note"):
        for currency in {v["extra"]["currency"] for v in values}:
            faces = [
                Decimal(v["extra"]["face_value"])
                for v in values
                if v["extra"]["kind"] == kind and v["extra"]["currency"] == currency
            ]
            assert faces == sorted(faces), (kind, currency)

    kinds = [v["extra"]["kind"] for v in values]
    assert kinds == sorted(kinds), "every coin precedes every note"


def test_a_lifecycle_vocabulary_keeps_its_sequence(client: TestClient) -> None:
    """Ordered -> Received -> ... is read constantly; alphabetical scrambles it."""
    codes = [
        v["code"] for v in client.get("/api/reference/item_status").json()["values"]
    ]

    assert codes.index("ordered") < codes.index("received") < codes.index("canceled")


def test_item_kind_keeps_its_curated_order(client: TestClient) -> None:
    """Coin and currency lead, ranked by how often a kind occurs.

    Alphabetical would put Bullion first, ahead of the two kinds that
    between them cover the whole collection.
    """
    codes = [v["code"] for v in client.get("/api/reference/item_kind").json()["values"]]

    assert codes.index("coin") < codes.index("currency") < codes.index("bullion")


def test_signature_combinations_keep_their_chronological_order(
    client: TestClient,
) -> None:
    """Oldest first: the picker narrows to a stretch of this timeline by year."""
    codes = [
        v["code"]
        for v in client.get("/api/reference/signature_combination").json()["values"]
    ]

    assert codes.index("tate_mellon") < codes.index("woods_mellon")


def test_a_value_added_late_still_sorts_alphabetically(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A mid-entry addition appears where it is looked for, not at the end."""
    response = client.post(
        "/api/reference/item_attribute",
        json={
            "code": "aardvark_test",
            "label": "Aardvark Test",
            "extra": {"applies_to": "any", "attribute_group": "variety"},
        },
        headers=admin_headers,
    )
    assert response.status_code == 201

    labels = [
        v["label"] for v in client.get("/api/reference/item_attribute").json()["values"]
    ]

    assert labels[0] == "Aardvark Test"


# ---------------------------------------------------------------------------
# Growing the vocabularies with use
# ---------------------------------------------------------------------------


def test_a_value_can_be_added_while_picking(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A value missing from a picker can be added from it.

    The organic-growth path: an operator entering an item that does not fit
    the shipped vocabulary adds the missing value rather than abandoning the
    entry or forcing it into an approximate one.
    """
    response = client.post(
        "/api/reference/grade",
        json={"code": "MS64PL", "label": "MS-64 Prooflike"},
        headers=admin_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["code"] == "MS64PL"

    # Marked as this installation's own, not shipped catalog.
    assert body["source"] == "manual"

    values = client.get("/api/reference/grade").json()["values"]
    assert "MS64PL" in {v["code"] for v in values}


def test_added_values_do_not_leak_into_a_shared_export(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """An installation's own values stay out of an export.

    `manual` is what keeps one collection's additions out of a catalog
    handed to another installation.
    """
    client.post(
        "/api/reference/grade",
        json={"code": "HOUSE_X", "label": "House grade"},
        headers=admin_headers,
    )
    from pathlib import Path
    from tempfile import TemporaryDirectory

    from app.seeding import export_reference_data

    with TemporaryDirectory() as tmp:
        export_reference_data(db, Path(tmp), sources=["seeded"])
        payload = json.loads((Path(tmp) / "grade.json").read_text(encoding="utf-8"))
    assert "HOUSE_X" not in {r["code"] for r in payload["grade"]}


def test_adding_a_duplicate_code_is_refused(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/reference/grade",
        json={"code": "65", "label": "Another MS-65"},
        headers=admin_headers,
    )
    assert response.status_code == 409


def test_adding_requires_an_administrator(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/reference/grade",
        json={"code": "ANY", "label": "Any"},
        headers=customer_headers,
    )
    assert response.status_code == 403


def test_a_table_specific_column_can_be_supplied(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/reference/error_type",
        json={
            "code": "clashed_dies",
            "label": "Clashed Dies",
            "extra": {"applies_to": "coin"},
        },
        headers=admin_headers,
    )
    assert response.status_code == 201
    assert response.json()["extra"]["applies_to"] == "coin"


def test_a_missing_required_column_is_explained(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A missing required column is explained, not a 500.

    A denomination without a currency or a face value cannot exist, and the
    caller should be told which, not handed a 500.
    """
    response = client.post(
        "/api/reference/denomination",
        json={"code": "usd_coin_3_00", "label": "Three Dollars"},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_an_unknown_column_is_named(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/reference/grade",
        json={"code": "X1", "label": "X", "extra": {"nonsense": 1}},
        headers=admin_headers,
    )
    assert response.status_code == 422
    assert "nonsense" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Renaming
# ---------------------------------------------------------------------------


def test_renaming_a_label_takes_effect_everywhere_at_once(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A rename reaches every record at once.

    The payoff for keeping one copy: every record refers to the value by
    foreign key, so there is nothing to migrate.
    """
    from app.models import InventoryItem, ItemKind

    from tests.test_schema import code_id, make_item

    item = make_item(
        db,
        item_kind_id=code_id(db, ItemKind, "coin"),
        grade_id=code_id(db, Grade, "65"),
    )

    response = client.patch(
        "/api/reference/grade/65",
        json={"label": "MS-65 (Gem Uncirculated)"},
        headers=admin_headers,
    )
    assert response.status_code == 200

    db.expire_all()
    refreshed = db.get_one(InventoryItem, item.id)
    assert refreshed.grade is not None
    assert refreshed.grade.label == "MS-65 (Gem Uncirculated)"
    # ...and the code, which is the contract, is untouched.
    assert refreshed.grade.code == "65"


def test_renaming_does_not_change_the_code(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Renaming changes the label and never the code.

    The code appears in saved filters and bookmarked searches, so renaming
    the label is precisely the operation that must not break them.
    """
    client.patch(
        "/api/reference/grade/64", json={"label": "Renamed"}, headers=admin_headers
    )
    values = client.get("/api/reference/grade").json()["values"]
    entry = next(v for v in values if v["code"] == "64")
    assert entry["label"] == "Renamed"


def test_a_value_can_be_retired_without_breaking_existing_records(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A value can be retired without breaking old records.

    Deleting is refused by the foreign keys anyway; retiring removes it from
    the pickers while leaving old records valid.
    """
    from app.models import ItemKind

    from tests.test_schema import code_id, make_item

    make_item(
        db,
        item_kind_id=code_id(db, ItemKind, "coin"),
        grade_id=code_id(db, Grade, "8"),
    )

    client.patch(
        "/api/reference/grade/8",
        json={"label": "VG-8", "is_active": False},
        headers=admin_headers,
    )

    offered = {v["code"] for v in client.get("/api/reference/grade").json()["values"]}
    assert "8" not in offered
    everything = client.get("/api/reference/grade?include_inactive=true").json()
    assert "8" in {v["code"] for v in everything["values"]}


def _value(client: TestClient, table: str, code: str) -> dict:
    body = client.get(f"/api/reference/{table}?include_inactive=true").json()
    return next(v for v in body["values"] if v["code"] == code)


@pytest.mark.parametrize(
    ("table", "code"),
    [
        ("item_status", "received"),
        ("disposition", "held"),
        ("strike_type", "proof"),
        ("storage_form", "single"),
        ("country", "US"),
        # `photo_names` names these from a filename's sequence number and
        # `photo_import` resolves them by code, so retiring one would fail
        # every import of a photograph filed as that side.
        ("image_role", "obverse"),
        ("image_role", "reverse"),
        ("image_role", "unassigned"),
    ],
)
def test_a_value_the_application_looks_up_cannot_be_retired(
    client: TestClient, admin_headers: dict[str, str], table: str, code: str
) -> None:
    """Retiring "received" would stop receiving; renaming it is only words."""
    label = _value(client, table, code)["label"]
    assert _value(client, table, code)["retirable"] is False

    refused = client.patch(
        f"/api/reference/{table}/{code}",
        json={"label": label, "is_active": False},
        headers=admin_headers,
    )
    assert refused.status_code == 409
    assert "looks it up by its code" in refused.json()["detail"]
    assert _value(client, table, code)["is_active"] is True

    renamed = client.patch(
        f"/api/reference/{table}/{code}",
        json={"label": f"{label} (renamed)"},
        headers=admin_headers,
    )
    assert renamed.status_code == 200
    assert renamed.json()["label"] == f"{label} (renamed)"


def test_a_descriptive_image_role_is_still_retirable(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Only the three roles the code names are protected, not the table.

    Without this the entry above could be widened to the whole `image_role`
    table and nothing would notice. "slab" is a description a person chooses
    from a list; no code looks it up.
    """
    assert _value(client, "image_role", "slab")["retirable"] is True


def test_a_descriptive_value_can_be_retired_and_restored(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    assert _value(client, "grade_designation", "FT")["retirable"] is True
    url = "/api/reference/grade_designation/FT"
    label = _value(client, "grade_designation", "FT")["label"]

    retired = client.patch(
        url, json={"label": label, "is_active": False}, headers=admin_headers
    )
    assert retired.json()["is_active"] is False
    # Retiring alone is not a change of wording: the shipped row stays shipped.
    assert retired.json()["source"] == "seeded"

    restored = client.patch(
        url, json={"label": label, "is_active": True}, headers=admin_headers
    )
    assert restored.json()["is_active"] is True


def test_a_renamed_value_keeps_its_name_through_a_seed_load(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A person's wording outranks the shipped one, so it becomes manual."""
    from app.models import GradeDesignation
    from app.seeding import _seed_table

    response = client.patch(
        "/api/reference/grade_designation/DCAM",
        json={"label": "  Deep   Cameo "},
        headers=admin_headers,
    )
    assert response.json()["label"] == "Deep Cameo"
    assert response.json()["source"] == "manual"

    shipped = [{"code": "DCAM", "label": "DCAM (Deep Cameo)", "sort_order": 10}]
    assert _seed_table(db, GradeDesignation, shipped) == {"skipped_manual": 1}
    assert _value(client, "grade_designation", "DCAM")["label"] == "Deep Cameo"


def test_a_blank_label_is_refused(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.patch(
        "/api/reference/grade_designation/DCAM",
        json={"label": "   "},
        headers=admin_headers,
    )
    assert response.status_code == 422
    assert _value(client, "grade_designation", "DCAM")["label"] == "DCAM (Deep Cameo)"


def test_only_staff_rename(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    url = "/api/reference/grade_designation/DCAM"
    assert client.patch(url, json={"label": "x"}).status_code == 401
    assert (
        client.patch(url, json={"label": "x"}, headers=customer_headers).status_code
        == 403
    )


def test_renaming_an_unknown_value_is_a_404(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.patch(
        "/api/reference/grade/NOPE", json={"label": "x"}, headers=admin_headers
    )
    assert response.status_code == 404


def test_renaming_requires_an_administrator(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    response = client.patch(
        "/api/reference/grade/65", json={"label": "x"}, headers=customer_headers
    )
    assert response.status_code == 403
