"""What the facts entered so far decide, before an item is saved.

`GET /api/defaults/note` and `/coin` answer the design the facts name, and
the note lookup says so when no issue of the denomination is of the series
typed.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

# -- a series with no issue -----------------------------------------------------


def _note_lookup(
    client: TestClient, headers: dict[str, str], **params: object
) -> dict[str, object]:
    """What the note-defaults route answers for the facts entered so far."""
    response = client.get("/api/defaults/note", params=params, headers=headers)
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def test_a_series_no_note_was_issued_in_is_said_and_the_real_ones_named(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """The owner's $2 1953-E: the series ends at 1953-C."""
    found = _note_lookup(
        client,
        admin_headers,
        denomination="usd_note_2",
        series_year=1953,
        series_letter="E",
    )
    assert found["signature_combination"] is None
    assert found["warning"] == (
        "No $2 note of Series 1953E is on record. "
        "Series 1953 on record: 1953, 1953A, 1953B, 1953C."
    )


def test_a_real_series_brings_its_signatures_and_no_warning(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    found = _note_lookup(
        client,
        admin_headers,
        denomination="usd_note_2",
        series_year=1953,
        series_letter="b",  # typed lower case
    )
    assert found["warning"] is None
    assert found["signature_combination"] == "smith_dillon"


def test_a_year_with_no_issue_lists_the_years_that_have_one(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    found = _note_lookup(
        client, admin_headers, denomination="usd_note_2", series_year=1954
    )
    warning = str(found["warning"])
    assert warning.startswith("No $2 note of Series 1954 is on record. $2 note series")
    assert "1928, 1953, 1963" in warning


def test_no_warning_where_the_record_does_not_cover_the_note(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A large-size note, or a denomination the record holds no issues of."""
    large = _note_lookup(
        client, admin_headers, denomination="usd_note_2", series_year=1917
    )
    assert large["warning"] is None
    foreign = _note_lookup(
        client, admin_headers, denomination="mxn_note_5", series_year=1953
    )
    assert foreign["warning"] is None


def _coin_lookup(
    client: TestClient, headers: dict[str, str], **params: object
) -> dict[str, object]:
    """What the coin-defaults route answers for the facts entered so far."""
    response = client.get("/api/defaults/coin", params=params, headers=headers)
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def test_a_coins_facts_name_its_design(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    found = _coin_lookup(client, admin_headers, denomination="usd_coin_0_10", year=1942)
    assert found["series"] == "winged_liberty_head_dime"


def test_a_boundary_year_suggests_no_design(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    # 1921 dollars are Morgans and Peace dollars both.
    found = _coin_lookup(client, admin_headers, denomination="usd_coin_1_00", year=1921)
    assert found["series"] is None


def test_a_notes_facts_name_its_design(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    found = _note_lookup(
        client, admin_headers, denomination="usd_note_1", series_year=1928
    )
    assert found["series"] == "funnyback"


def test_a_chosen_seal_is_evidence_for_the_design(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    # Every ordinary $5 1934A is also a candidate Hawaii note; the brown seal
    # is what makes it one.
    facts = {"denomination": "usd_note_5", "series_year": 1934, "series_letter": "a"}
    plain = _note_lookup(client, admin_headers, **facts)
    brown = _note_lookup(client, admin_headers, **facts, seal_color="brown")
    assert plain["series"] is None
    assert brown["series"] == "hawaii"
