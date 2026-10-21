"""Signature combinations, and narrowing the vocabulary to a year in office.

`?year=` answers who held office in a year (`routers.reference._limit_to_year`).
It is not the pairs a note of that *series* can carry -- a lettered series is
printed later, under later officials -- which `GET /api/friedberg/signatures`
answers from the issue facts (`test_friedberg.py`).

Neither vocabulary is the shop's, so each is read as a manager.
"""

from __future__ import annotations

import pytest
from app.models import SignatureCombination
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def _values(
    client: TestClient, headers: dict[str, str], table: str, query: str = ""
) -> list[dict]:
    """The vocabulary's values as the route returns them to this caller."""
    response = client.get(f"/api/reference/{table}{query}", headers=headers)
    assert response.status_code == 200, response.text
    return list(response.json()["values"])


def _labels(client: TestClient, headers: dict[str, str], query: str = "") -> list[str]:
    """The labels of the signature combinations the route returns, in its order."""
    return [
        row["label"] for row in _values(client, headers, "signature_combination", query)
    ]


def test_the_vocabulary_is_seeded(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    labels = _labels(client, admin_headers)
    assert "Julian / Morgenthau" in labels
    assert "Granahan / Dillon" in labels


def test_treasurer_and_secretary_come_through(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A picker needs the names, not only the pair's code."""
    values = _values(client, admin_headers, "signature_combination")
    julian = next(v for v in values if v["code"] == "julian_morgenthau")
    assert julian["extra"]["treasurer"] == "William A. Julian"
    assert julian["extra"]["secretary"] == "Henry Morgenthau Jr."
    assert "register" not in julian["extra"]


def test_a_large_size_pair_has_a_register_in_place_of_a_secretary(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Speelman signed as Register of the Treasury, beside Treasurer White."""
    values = _values(client, admin_headers, "signature_combination")
    pair = next(v for v in values if v["code"] == "speelman_white")
    assert pair["label"] == "Speelman / White"
    assert pair["extra"]["register"] == "Harley V. Speelman"
    assert pair["extra"]["treasurer"] == "Frank White"
    assert "secretary" not in pair["extra"]


def test_a_pair_has_a_secretary_or_a_register_never_both(db: Session) -> None:
    """Two cosigners, or none, is no pair a note was signed by."""
    for secretary, register in (("A. Secretary", "A. Register"), (None, None)):
        with pytest.raises(IntegrityError), db.begin_nested():
            db.add(
                SignatureCombination(
                    code=f"test_pair_{secretary is None}",
                    label="Test / Pair",
                    treasurer="A. Treasurer",
                    secretary=secretary,
                    register=register,
                )
            )
            db.flush()


def test_a_year_narrows_to_the_possible_pairs(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A year gives the pairs in office that year, and no others.

    Julian and Morgenthau alone held office in 1936, Priest and Humphrey
    alone in 1954.
    """
    assert _labels(client, admin_headers, "?year=1936") == ["Julian / Morgenthau"]
    assert _labels(client, admin_headers, "?year=1954") == ["Priest / Humphrey"]
    # Two pairs signed in 1969: Granahan/Barr until January (the $1 1963B),
    # then Elston/Kennedy. Both are possible, in the order they were used --
    # signature_combination is a curated, chronological sequence, so it
    # keeps sort_order rather than coming back alphabetical.
    assert _labels(client, admin_headers, "?year=1969") == [
        "Granahan / Barr",
        "Elston / Kennedy",
    ]


def test_a_year_outside_every_term_offers_nothing(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Better empty than wrong: no seeded pair signed in 1850."""
    assert _labels(client, admin_headers, "?year=1850") == []


def test_a_table_without_terms_is_unfiltered(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """The parameter narrows where it can; it is not a requirement.

    Grades have no term, so asking for a year must not silently empty the
    picker -- a form asking for both would then offer no grades at all.
    """
    with_year = _values(client, admin_headers, "grade", "?year=1936")
    without = _values(client, admin_headers, "grade")
    assert len(with_year) == len(without) > 0
