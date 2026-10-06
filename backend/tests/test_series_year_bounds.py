"""A note's series year is bounded the same way wherever it is sent.

Creating an item, correcting one and cataloguing a Friedberg
number all take `schemas.SeriesYear`: 1690 (the first paper money issued in
America) to 2200, so a pre-1861 year the collection holds is accepted on
every path.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.schemas import (
    FriedbergNumberCreate,
    InventoryItemUpdate,
    ItemCreate,
)
from pydantic import BaseModel, ValidationError

REQUIRED: dict[type[BaseModel], dict[str, Any]] = {
    ItemCreate: {"purchase_order_id": 1, "item_kind": "currency", "source_title": "x"},
    InventoryItemUpdate: {},
    FriedbergNumberCreate: {"fr_number": "1617"},
}


@pytest.mark.parametrize("schema", list(REQUIRED), ids=lambda s: s.__name__)
@pytest.mark.parametrize("year", [1690, 1801, 2200])
def test_a_series_year_in_bounds_is_accepted(
    schema: type[BaseModel], year: int
) -> None:
    body = schema(**REQUIRED[schema], series_year=year)
    assert body.series_year == year  # type: ignore[attr-defined]


@pytest.mark.parametrize("schema", list(REQUIRED), ids=lambda s: s.__name__)
@pytest.mark.parametrize("year", [1689, 2201])
def test_a_series_year_out_of_bounds_is_refused(
    schema: type[BaseModel], year: int
) -> None:
    with pytest.raises(ValidationError):
        schema(**REQUIRED[schema], series_year=year)
