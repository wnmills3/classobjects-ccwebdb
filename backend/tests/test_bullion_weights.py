"""Guessed weights for bars, rounds and medals (`app.bullion_weights`).

What the pass reads out of an item's words, what it takes from an item's
peers, and that a guess is recorded as one and never replaces a value.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.bullion_weights import apply, fineness_in, plan, weight_in
from app.field_sources import HELD, derived_fields, record_derived
from app.models import BullionForm, InventoryItem, ItemFieldChange, Metal, User
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.builders import ItemFactory, code_id


@pytest.mark.parametrize(
    ("text", "ozt"),
    [
        ("5 oz. .999 Fine Silver Bar", "5.000000"),
        ("1-oz Silver Eagle", "1.000000"),
        ("1/2 oz silver round", "0.500000"),
        ("1/10 oz Gold Maple", "0.100000"),
        (".5oz", "0.500000"),
        ("8G 22K GOLD 1911 COIN", "0.257206"),
        ("Silver 40g The Great Canadian Landmarks", "1.286030"),
        ("1 Gram .999 Fine Silver", "0.032151"),
        ("Copper Bar 500 gr", "16.075373"),
        ("1 Kilo copper", "32.150747"),
        ("One Ounce Silver Proof Coin", "1.000000"),
        ("1/4 Grain .999 Fine Gold Bar", "0.000521"),
        ("1 troy ounce", "1.000000"),
        # The same weight said twice is one weight.
        ("1 oz round - 1 oz .999", "1.000000"),
    ],
)
def test_a_weight_is_read_out_of_the_words(text: str, ozt: str) -> None:
    assert weight_in(text) == Decimal(ozt)


@pytest.mark.parametrize(
    "text",
    [
        "Silver Round",
        # A lot's description: two different weights say nothing about one piece.
        "ten 1 oz rounds, 10 oz in all",
        "E-CP-012501 - 12/01/24 - EL365",
        "2025 $1 American Silver Eagle PCGS MS70 1 of 500",
        ".30218 Silver Coins & Bullion 20x ASE 2026",
        "5000 oz",
        None,
    ],
)
def test_words_with_no_single_weight_give_none(text: str | None) -> None:
    assert weight_in(text) is None


@pytest.mark.parametrize(
    ("text", "fineness"),
    [
        ("5 oz. .999 Fine Silver Bar", "0.9990"),
        ("Canada $5 .9999 pure round", "0.9999"),
        ("999 Fine Troy Ounce", "0.9990"),
        ("Franklin Mint Sterling Rounds", "0.9250"),
        ("8G 22K GOLD 1911 COIN", "0.9167"),
        ("90% silver", "0.9000"),
        # Written with its zero.
        ("0.999 Silver Round", "0.9990"),
        ("Medal, 0.925 silver", "0.9250"),
    ],
)
def test_a_fineness_is_read_out_of_the_words(text: str, fineness: str) -> None:
    assert fineness_in(text) == Decimal(fineness)


@pytest.mark.parametrize(
    "text",
    [
        # A lot code, not a fineness.
        "Item #63 - SLABATHON .708",
        # The metal named is the surface, not the piece.
        "24K GOLD Foil Layered Souvenirs",
        "24K gold plated round",
        ".999 silver and .925 silver",
        # The decimals of a larger number, not a fineness.
        "Lot 12.925 silver round",
        "Silver Round",
    ],
)
def test_words_with_no_single_fineness_give_none(text: str) -> None:
    assert fineness_in(text) is None


def _bullion(
    db: Session,
    make_item: ItemFactory,
    *,
    form: str | None = "round",
    metal: str | None = "silver",
    **fields: object,
) -> InventoryItem:
    """A bullion item of this form and metal, its words stating no weight."""
    fields.setdefault("title", "Round")
    fields.setdefault("description", "As shown on screen.")
    item = make_item(kind="bullion", year_start=None, **fields)
    item.bullion_form_id = code_id(db, BullionForm, form) if form else None
    item.metal_id = code_id(db, Metal, metal) if metal else None
    db.flush()
    return item


def _guesses(db: Session, item: InventoryItem) -> dict[str, tuple[Decimal, str]]:
    """What the pass would guess for the item: value and rule, by column."""
    return {
        g.column: (g.value, g.rule) for g in plan(db).guesses if g.item_id == item.id
    }


def test_an_item_takes_the_weight_and_fineness_its_own_words_state(
    db: Session, make_item: ItemFactory
) -> None:
    bar = _bullion(db, make_item, form="bar", title="5 oz. .999 Fine Silver Bar")
    db.commit()

    assert _guesses(db, bar) == {
        "gross_weight_ozt": (Decimal("5.000000"), "weight_text"),
        "fineness": (Decimal("0.9990"), "weight_text"),
    }


def test_an_item_with_a_fine_weight_or_a_value_already_is_left(
    db: Session, make_item: ItemFactory
) -> None:
    done = _bullion(
        db, make_item, title="5 oz .999 bar", fine_weight_ozt=Decimal("4.995")
    )
    partly = _bullion(db, make_item, title="5 oz .999 bar", fineness=Decimal("0.9250"))
    db.commit()

    assert _guesses(db, done) == {}
    # Its fineness is a person's: only the empty weight is guessed.
    assert _guesses(db, partly) == {
        "gross_weight_ozt": (Decimal("5.000000"), "weight_text")
    }


def _peers(db: Session, make_item: ItemFactory, *, form: str = "round") -> None:
    """Six rounds of an ounce and two of five: three in four agree."""
    for gross in ["1"] * 6 + ["5"] * 2:
        _bullion(
            db,
            make_item,
            form=form,
            gross_weight_ozt=Decimal(gross),
            fineness=Decimal("0.9990"),
            fine_weight_ozt=Decimal(gross) * Decimal("0.999"),
        )


def test_an_item_with_nothing_in_its_words_takes_what_its_peers_hold(
    db: Session, make_item: ItemFactory
) -> None:
    _peers(db, make_item)
    plain = _bullion(db, make_item)
    other_form = _bullion(db, make_item, form="bar")
    other_metal = _bullion(db, make_item, metal="gold")
    db.commit()

    assert _guesses(db, plain) == {
        "gross_weight_ozt": (Decimal("1.000000"), "weight_peers"),
        "fineness": (Decimal("0.9990"), "weight_peers"),
    }
    # Peers are of the same form and the same metal, or they are not peers.
    assert _guesses(db, other_form) == {}
    assert _guesses(db, other_metal) == {}


def test_a_peer_weight_is_not_put_on_an_item_of_another_fineness(
    db: Session, make_item: ItemFactory
) -> None:
    _peers(db, make_item)
    sterling = _bullion(db, make_item, title="Franklin Mint Sterling Rounds")
    db.commit()

    # The fineness its words state, and no weight: a sterling round is not
    # one of the .999 ounces around it.
    assert _guesses(db, sterling) == {"fineness": (Decimal("0.9250"), "weight_text")}


def test_peers_with_no_fineness_recorded_are_taken_for_fine_metal(
    db: Session, make_item: ItemFactory
) -> None:
    """A fine weight and no fineness: an ounce of .999, not of sterling."""
    for _ in range(6):
        _bullion(db, make_item, fine_weight_ozt=Decimal("1"))
    sterling = _bullion(db, make_item, title="Franklin Mint Sterling Rounds")
    marked_sterling = _bullion(db, make_item, fineness=Decimal("0.9250"))
    fine = _bullion(db, make_item, fineness=Decimal("0.9990"))
    unknown = _bullion(db, make_item)
    db.commit()

    one_ounce = {"fine_weight_ozt": (Decimal("1"), "weight_peers")}
    assert _guesses(db, sterling) == {"fineness": (Decimal("0.9250"), "weight_text")}
    assert _guesses(db, marked_sterling) == {}
    assert _guesses(db, fine) == one_ounce
    assert _guesses(db, unknown) == one_ounce


def test_peers_with_a_gross_weight_and_no_fineness_give_their_fine_weight_too(
    db: Session, make_item: ItemFactory
) -> None:
    """Gross weight alone leads to no fine weight: there is nothing to multiply."""
    for _ in range(6):
        _bullion(
            db,
            make_item,
            gross_weight_ozt=Decimal("1"),
            fine_weight_ozt=Decimal("1"),
        )
    plain = _bullion(db, make_item)
    db.commit()

    assert _guesses(db, plain) == {
        "gross_weight_ozt": (Decimal("1"), "weight_peers"),
        "fine_weight_ozt": (Decimal("1"), "weight_peers"),
    }


def test_too_few_peers_give_no_guess_however_well_they_agree(
    db: Session, make_item: ItemFactory
) -> None:
    def ounce(form: str) -> None:
        """One round or bar of an ounce of .999, a peer for its form."""
        _bullion(
            db,
            make_item,
            form=form,
            gross_weight_ozt=Decimal("1"),
            fineness=Decimal("0.9990"),
            fine_weight_ozt=Decimal("0.999"),
        )

    for _ in range(4):
        ounce("round")
    for _ in range(5):
        ounce("bar")
    few = _bullion(db, make_item)
    enough = _bullion(db, make_item, form="bar")
    db.commit()

    # Four of four agree, and four is not enough; five of five is.
    assert _guesses(db, few) == {}
    assert set(_guesses(db, enough)) == {"gross_weight_ozt", "fineness"}


def test_peers_that_do_not_mostly_agree_give_no_guess(
    db: Session, make_item: ItemFactory
) -> None:
    for gross in ["1", "1", "1", "2", "2", "5", "5", "10"]:
        _bullion(
            db,
            make_item,
            gross_weight_ozt=Decimal(gross),
            fineness=Decimal("0.9990"),
            fine_weight_ozt=Decimal(gross) * Decimal("0.999"),
        )
    plain = _bullion(db, make_item)
    db.commit()

    assert _guesses(db, plain) == {}
    assert plain.item_code in plan(db).no_guess


def test_a_guess_is_not_evidence_for_the_next_guess(
    db: Session, make_item: ItemFactory
) -> None:
    """Without this the pass would talk itself into a weight, run by run."""
    for _ in range(8):
        guessed = _bullion(
            db,
            make_item,
            gross_weight_ozt=Decimal("1"),
            fineness=Decimal("0.9990"),
            fine_weight_ozt=Decimal("0.999"),
        )
        record_derived(db, guessed.id, ["gross_weight_ozt"], "weight_peers")
    plain = _bullion(db, make_item)
    db.commit()

    assert _guesses(db, plain) == {}


def test_a_field_a_person_emptied_is_not_guessed(
    db: Session, make_item: ItemFactory
) -> None:
    held = _bullion(db, make_item, title="5 oz .999 bar")
    record_derived(db, held.id, ["gross_weight_ozt"], HELD)
    db.commit()

    assert _guesses(db, held) == {"fineness": (Decimal("0.9990"), "weight_text")}


def test_applying_writes_the_guess_works_out_fine_weight_and_logs_it(
    db: Session, make_item: ItemFactory, admin_user: User
) -> None:
    bar = _bullion(db, make_item, form="bar", title="5 oz. .999 Fine Silver Bar")
    db.commit()

    counts = apply(db, plan(db), admin_user.id)
    db.commit()

    db.refresh(bar)
    assert (bar.gross_weight_ozt, bar.fineness, bar.fine_weight_ozt) == (
        Decimal("5.000000"),
        Decimal("0.9990"),
        Decimal("4.995000"),
    )
    assert counts == {"items": 1, "values": 2, "fine_weights": 1}
    assert derived_fields(db, bar.id) == {
        "gross_weight_ozt": "weight_text",
        "fineness": "weight_text",
        "fine_weight_ozt": "weight",
    }
    logged = db.scalars(
        select(ItemFieldChange).where(ItemFieldChange.inventory_item_id == bar.id)
    ).all()
    assert sorted((c.field_name, c.changed_by_id) for c in logged) == [
        ("fine_weight_ozt", admin_user.id),
        ("fineness", admin_user.id),
        ("gross_weight_ozt", admin_user.id),
    ]
    # Run again, nothing is left to do: it has a fine weight now.
    assert _guesses(db, bar) == {}
