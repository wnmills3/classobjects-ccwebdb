"""Series classification and the alias search.

The alias table earns its place only if a search for a formal name finds items
that never use it, and the reverse. These fail if the alias join is removed.

The series vocabulary is seeded, so these read it rather than inventing one --
which also means a mistake in `series.json` shows up here.
"""

from __future__ import annotations

from collections.abc import Callable

from app.inventory_search import COIN_VIEW, count_facets, search, series_ids_matching
from app.models import InventoryItem, Series
from app.series_match import build_rules, match
from sqlalchemy import select
from sqlalchemy.orm import Session

ItemFactory = Callable[..., InventoryItem]


def _series(db: Session, code: str) -> Series:
    return db.execute(select(Series).where(Series.code == code)).scalar_one()


def test_alias_resolves_to_its_series(db: Session) -> None:
    """Both names reach the same series, which is the whole point."""
    mercury = _series(db, "winged_liberty_head_dime")

    assert series_ids_matching(db, "Mercury") == [mercury.id]
    assert series_ids_matching(db, "merc") == [mercury.id]
    assert series_ids_matching(db, "Winged Liberty") == [mercury.id]
    assert series_ids_matching(db, "") == []


def test_one_alias_may_span_several_series(db: Session) -> None:
    """A nickname is not owned by one design: any large silver dollar."""
    found = set(series_ids_matching(db, "Cartwheel"))
    expected = {
        _series(db, "morgan_dollar").id,
        _series(db, "peace_dollar").id,
    }
    assert found == expected


def test_search_finds_an_item_by_a_name_its_description_lacks(
    db: Session, make_item: ItemFactory
) -> None:
    """The reason the alias table exists.

    The description says "Mercury" and never "Winged Liberty Head". Measured
    over the real collection: 104 rows say the nickname and none say the
    formal name, so matching description text alone finds one and misses the
    other entirely.
    """
    mercury = _series(db, "winged_liberty_head_dime")
    make_item(description="1945 Silver Mercury Dime", series_id=mercury.id)

    by_nickname, _ = search(db, COIN_VIEW, params={}, query="Mercury")
    by_formal, _ = search(db, COIN_VIEW, params={}, query="Winged Liberty Head")

    assert len(by_nickname) == 1
    assert len(by_formal) == 1, "the formal name must reach it through the series"
    assert by_formal[0]["item_code"] == by_nickname[0]["item_code"]


def test_unrelated_search_does_not_match(db: Session, make_item: ItemFactory) -> None:
    """The alias join must not widen a search to everything."""
    mercury = _series(db, "winged_liberty_head_dime")
    make_item(description="1945 Silver Mercury Dime", series_id=mercury.id)
    rows, _ = search(db, COIN_VIEW, params={}, query="Saint-Gaudens")
    assert rows == []


def test_ambiguous_terms_need_a_denomination(db: Session) -> None:
    """Barber names three series; alone it means nothing.

    Guessing one would misfile the coin, and every price looked up against it
    afterwards would be for the wrong thing.
    """
    rules = build_rules(db)
    assert match("1899 Barber coin", None, rules) == set()
    assert match("1899 Barber coin", "Quarter", rules) == {"barber_quarter"}
    assert match("1899 Barber coin", "Dime", rules) == {"barber_dime"}


def test_two_series_in_one_description_stays_ambiguous(db: Session) -> None:
    """A Franklin Pierce Presidential Dollar is not a Franklin Half.

    Real case: CC-000371 in the collection matches both, and taking the first
    match would have filed a presidential dollar as a Franklin half.
    """
    rules = build_rules(db)
    found = match("2010 D FRANKLIN PIERCE PRESIDENTIAL DOLLAR ANACS", None, rules)
    assert len(found) > 1, "must be recognised as ambiguous, not silently resolved"


def test_series_facet_counts_by_indexed_key(
    db: Session, make_item: ItemFactory
) -> None:
    mercury = _series(db, "winged_liberty_head_dime")
    make_item(description="a dime", series_id=mercury.id)
    make_item(description="another dime", series_id=mercury.id)

    facets = count_facets(db, COIN_VIEW, params={})
    counts = {row["value"]: row["count"] for row in facets["series"]}
    assert counts.get("winged_liberty_head_dime") == 2


def test_series_filter_selects_only_that_series(
    db: Session, make_item: ItemFactory
) -> None:
    make_item(
        description="a dime", series_id=_series(db, "winged_liberty_head_dime").id
    )
    make_item(description="a dollar", series_id=_series(db, "morgan_dollar").id)

    rows, total = search(db, COIN_VIEW, params={"series": "morgan_dollar"}, query=None)
    assert total == 1
    assert rows[0]["series"] == "morgan_dollar"
    assert rows[0]["series_label"] == "Morgan Dollar"
