"""No shipped seed file carries a publisher's catalogue number.

docs/reference-data.md: a Friedberg or Pick number is one publisher's
arrangement, recorded per note by the owner and never shipped. Every key,
value and comment of every file under backend/data/reference/ is scanned.
No database is involved.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from app.seeding import DATA_DIR, iter_seed_files

#: A Friedberg or Pick number as it is written: Fr. 9901, Fr 9901,
#: Friedberg 9901, Pick 9901, P-9901.
CATALOGUE_NUMBER = re.compile(
    r"\bFr\.?\s*\d|Friedberg\s*\d|\bPick\s*\d|\bP-\d", re.IGNORECASE
)


def _strings(node: object) -> Iterator[str]:
    """Every key and string value in a parsed JSON document."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)
    elif isinstance(node, str):
        yield node


def _catalogue_numbers(path: Path) -> list[str]:
    """Each catalogue-number-shaped text in one seed file."""
    document = json.loads(path.read_text(encoding="utf-8"))
    return [
        text for text in _strings(document) if CATALOGUE_NUMBER.search(text) is not None
    ]


def test_the_seed_files_are_found() -> None:
    assert len(list(iter_seed_files())) >= 10


@pytest.mark.parametrize("path", list(iter_seed_files()), ids=lambda p: p.name)
def test_no_seed_file_ships_a_catalogue_number(path: Path) -> None:
    found = _catalogue_numbers(path)
    assert not found, f"{path.relative_to(DATA_DIR.parent)}: {found}"


@pytest.mark.parametrize(
    "text",
    ["Fr. 9901", "Fr9902", "Friedberg 9903", "Pick 9904a", "P-9905", "fr 9906"],
)
def test_each_written_form_is_caught(text: str, tmp_path: Path) -> None:
    seed = tmp_path / "planted.json"
    seed.write_text(
        json.dumps({"_comment": ["x"], "note_issue": [{"variant": text}]}),
        encoding="utf-8",
    )
    assert _catalogue_numbers(seed) == [text]


def test_a_key_or_comment_is_scanned_too(tmp_path: Path) -> None:
    seed = tmp_path / "planted.json"
    seed.write_text(
        json.dumps({"_comment": ["see Fr. 9901"], "Pick 9901": []}),
        encoding="utf-8",
    )
    assert _catalogue_numbers(seed) == ["see Fr. 9901", "Pick 9901"]


def test_ordinary_text_is_not_caught(tmp_path: Path) -> None:
    seed = tmp_path / "planted.json"
    seed.write_text(
        json.dumps({"series": [{"label": "Franklin Half Dollar", "note": "PCGS 65"}]}),
        encoding="utf-8",
    )
    assert _catalogue_numbers(seed) == []
