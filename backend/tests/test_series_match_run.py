"""Characterisation tests for `series_match.run`.

test_series covers `build_rules` and `match`; this covers `run` -- the
function that walks the collection and writes the classification, the part a
refactor could break silently.

Written against the code as it stands: their job is to detect a change, not to
argue what the behavior ought to be.
"""

from __future__ import annotations

from app.models import ProvenanceSource, Series
from app.series_match import run
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.builders import ItemFactory

# Nothing in the seeded series vocabulary matches this.
UNRECOGNISABLE = {"title": "Plain metal disc", "description": "No series here."}


def _series_id(db: Session, code: str) -> int:
    """The id of the series with this code."""
    return db.execute(select(Series.id).where(Series.code == code)).scalar_one()


def test_a_recognisable_description_is_matched(
    db: Session, make_item: ItemFactory
) -> None:
    item = make_item(title="1881-S Morgan Silver Dollar", description="Nice strike.")

    stats = run(db, commit=False)

    assert stats["matched"] >= 1
    # A dry run reports without writing.
    db.refresh(item)
    assert item.series_id is None


def test_a_commit_writes_the_series(db: Session, make_item: ItemFactory) -> None:
    item = make_item(title="1881-S Morgan Silver Dollar", description="Nice strike.")

    stats = run(db, commit=True)

    assert stats["written"] >= 1
    db.refresh(item)
    assert item.series_id == _series_id(db, "morgan_dollar")


def test_a_nickname_covering_two_series_is_left_alone(
    db: Session, make_item: ItemFactory
) -> None:
    # "Cartwheel" is any large silver dollar, so it names both the Morgan and
    # the Peace. Two candidates is a mixed lot, not a classification. Dated
    # 1921, when both were struck: in any other year the date would decide.
    item = make_item(
        title="Cartwheel", description="A big silver dollar.", year_start=1921
    )

    stats = run(db, commit=True)

    assert stats["ambiguous"] >= 1
    db.refresh(item)
    assert item.series_id is None


def test_an_unrecognisable_item_is_counted_not_guessed(
    db: Session, make_item: ItemFactory
) -> None:
    item = make_item(**UNRECOGNISABLE)

    stats = run(db, commit=True)

    assert stats["no_match"] >= 1
    db.refresh(item)
    assert item.series_id is None


def test_an_already_classified_item_is_not_revisited(
    db: Session, make_item: ItemFactory
) -> None:
    peace = _series_id(db, "peace_dollar")
    # Deliberately the "wrong" series for the text: run must not correct it,
    # because the query only looks at items with no series at all.
    item = make_item(
        title="1881-S Morgan Silver Dollar", description="x", series_id=peace
    )

    run(db, commit=True)

    db.refresh(item)
    assert item.series_id == peace


def test_a_seeded_item_becomes_derived_once_classified(
    db: Session, make_item: ItemFactory
) -> None:
    # The provenance has to move: a value the matcher worked out is not one
    # that shipped with the catalog.
    item = make_item(
        title="1881-S Morgan Silver Dollar",
        description="Nice strike.",
        source=ProvenanceSource.seeded,
    )

    run(db, commit=True)

    db.refresh(item)
    assert item.source is ProvenanceSource.derived


def test_counts_cover_every_item_examined(db: Session, make_item: ItemFactory) -> None:
    make_item(title="1881-S Morgan Silver Dollar", description="a")
    # Dated 1921, when both the designs it names were struck.
    make_item(title="Cartwheel", description="b", year_start=1921)
    # Names a design not struck in the item's year.
    make_item(title="Franklin Half Dollar", description="c")
    make_item(**UNRECOGNISABLE)

    stats = run(db, commit=False)

    # Each item under exactly one outcome: none dropped, none counted twice.
    assert dict(stats) == {
        "matched": 1,
        "ambiguous": 1,
        "contradicted": 1,
        "no_match": 1,
    }


def test_a_round_or_medal_is_not_a_coin_design_for_sharing_its_name(
    db: Session, make_item: ItemFactory
) -> None:
    # A bar, round or medal has no denomination or year to check a name
    # against, so a design struck at one face value is not believed of it;
    # a bullion design, which has none recorded, still is.
    buffalo = make_item(
        kind="bullion",
        title="1 oz Silver Buffalo Round",
        description="Generic round.",
        year_start=None,
    )
    medal = make_item(
        kind="medal",
        title="Abraham Lincoln Medal",
        description="Bronze.",
        year_start=None,
    )
    eagle = make_item(
        kind="bullion",
        title="American Silver Eagle 1 oz",
        description="In a capsule.",
        year_start=None,
    )

    stats = run(db, commit=True)

    for item in (buffalo, medal, eagle):
        db.refresh(item)
    assert buffalo.series_id is None
    assert medal.series_id is None
    assert eagle.series_id == _series_id(db, "american_silver_eagle")
    assert stats["contradicted"] == 2
