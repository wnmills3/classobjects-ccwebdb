"""Characterisation tests for `series_match.run`.

test_series covers `build_rules` and `match`; `run` -- the function that walks
the collection and writes the classification -- was not covered, which is the
part a refactor could break silently.

Written against the code as it stands: their job is to detect a change, not to
argue what the behaviour ought to be.
"""

from __future__ import annotations

from collections.abc import Callable

from app.models import InventoryItem, ProvenanceSource, Series
from app.series_match import run
from sqlalchemy import select
from sqlalchemy.orm import Session

ItemFactory = Callable[..., InventoryItem]

# Nothing in the seeded series vocabulary matches this.
UNRECOGNISABLE = {"title": "Plain metal disc", "description": "No series here."}


def _series_id(db: Session, code: str) -> int:
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
    # the Peace. Two candidates is a mixed lot, not a classification.
    item = make_item(title="Cartwheel", description="A big silver dollar.")

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
    # that shipped with the catalogue.
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
    make_item(title="Cartwheel", description="b")
    make_item(**UNRECOGNISABLE)

    stats = run(db, commit=False)

    assert stats["matched"] + stats["ambiguous"] + stats["no_match"] >= 3
