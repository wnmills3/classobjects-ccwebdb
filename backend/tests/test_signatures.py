"""Signature combinations, and narrowing a picker by a note's series year.

Signatures are what separate one catalogue variant of a note from another, so
offering the wrong one is a wrong lookup rather than a cosmetic slip. The year
filter exists to make the wrong choice unavailable.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _labels(client: TestClient, query: str = "") -> list[str]:
    response = client.get(f"/api/reference/signature_combination{query}")
    assert response.status_code == 200, response.text
    return [row["label"] for row in response.json()["values"]]


def test_the_vocabulary_is_seeded(client: TestClient) -> None:
    labels = _labels(client)
    assert "Julian / Morgenthau" in labels
    assert "Granahan / Dillon" in labels


def test_treasurer_and_secretary_come_through(client: TestClient) -> None:
    """A picker needs the names, not only the pair's code."""
    values = client.get("/api/reference/signature_combination").json()["values"]
    julian = next(v for v in values if v["code"] == "julian_morgenthau")
    assert julian["extra"]["treasurer"] == "William A. Julian"
    assert julian["extra"]["secretary"] == "Henry Morgenthau Jr."


def test_a_year_narrows_to_the_possible_pairs(client: TestClient) -> None:
    """The point of the filter.

    A 1935A note was printed in 1936 and can only carry Julian/Morgenthau.
    Offering the other ten invites a wrong catalogue lookup.
    """
    assert _labels(client, "?year=1936") == ["Julian / Morgenthau"]
    assert _labels(client, "?year=1954") == ["Priest / Humphrey"]
    assert _labels(client, "?year=1969") == ["Elston / Kennedy"]


def test_a_year_outside_every_term_offers_nothing(client: TestClient) -> None:
    """Better empty than wrong: no seeded pair signed in 1850."""
    assert _labels(client, "?year=1850") == []


def test_a_table_without_terms_is_unfiltered(client: TestClient) -> None:
    """The parameter narrows where it can; it is not a requirement.

    Grades have no term, so asking for a year must not silently empty the
    picker -- a form asking for both would then offer no grades at all.
    """
    with_year = client.get("/api/reference/grade?year=1936").json()["values"]
    without = client.get("/api/reference/grade").json()["values"]
    assert len(with_year) == len(without) > 0
