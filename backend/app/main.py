"""FastAPI application entry point."""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import auctions, sales_writes
from .config import settings
from .routers import (
    acquisitions,
    auth,
    catalog,
    customers,
    defaults,
    friedberg,
    image_links,
    images,
    inventory,
    lots,
    offers,
    orders,
    reference,
    sales_venues,
    users,
)
from .routers import (
    auctions as auctions_router,
)

app = FastAPI(
    title="ccwebdb",
    description="Numismatic and currency inventory and sales platform",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix=settings.api_prefix)
app.include_router(catalog.router, prefix=settings.api_prefix)
app.include_router(images.router, prefix=settings.api_prefix)
app.include_router(image_links.router, prefix=settings.api_prefix)
app.include_router(inventory.router, prefix=settings.api_prefix)
app.include_router(reference.router, prefix=settings.api_prefix)
app.include_router(defaults.router, prefix=settings.api_prefix)
app.include_router(orders.router, prefix=settings.api_prefix)
app.include_router(users.router, prefix=settings.api_prefix)
app.include_router(customers.router, prefix=settings.api_prefix)
app.include_router(acquisitions.vendors_router, prefix=settings.api_prefix)
app.include_router(acquisitions.purchase_orders_router, prefix=settings.api_prefix)
app.include_router(acquisitions.storage_locations_router, prefix=settings.api_prefix)
app.include_router(friedberg.friedberg_router, prefix=settings.api_prefix)
app.include_router(friedberg.item_router, prefix=settings.api_prefix)
app.include_router(sales_venues.router, prefix=settings.api_prefix)
app.include_router(offers.router, prefix=settings.api_prefix)
app.include_router(lots.router, prefix=settings.api_prefix)
app.include_router(auctions_router.router, prefix=settings.api_prefix)


# ---------------------------------------------------------------------------
# Exception handlers -- ruling R20 (docs/specs/selling-design.md, Task 5)
#
# Registered **per class**, not decided by an `except` clause's position.
# `sales_writes.SaleInputInvalid` subclasses `sales_writes.SaleRefused`, and
# `auctions.SettlementInputInvalid` subclasses `auctions.AuctionRefused` --
# each pair is bad input (422) beside a genuine conflict (409), and an
# ordered `try`/`except` reversed by a later edit would silently turn every
# 422 into a 409 with nothing catching the mistake: mypy accepts either
# order, because the narrower type is still assignable to the wider one.
#
# A registered handler has no order to get wrong. Starlette's
# `ExceptionMiddleware._lookup_exception_handler` walks the raised
# exception's `__mro__` and returns the **first class in that MRO** with a
# registered handler -- `SettlementInputInvalid`'s own entry, found before
# `AuctionRefused`'s, regardless of which was registered first or which
# `app.add_exception_handler` call appears first in this file. The hazard
# above is not merely avoided; it has no code path left to reintroduce it.
#
# `test_settlement_input_invalid_is_a_422_not_a_409` and
# `test_a_plain_auction_refused_is_still_a_409`
# (`backend/tests/test_auctions_api.py`) are what stand behind this, the same
# way `test_sale_input_invalid_from_record_sale_is_a_422_not_a_409` stands
# behind `routers/offers.py`'s own ordered `except` clauses for the same
# pair -- **not migrated to a handler here**, so that one keeps its own,
# different failure mode (a silent status swap) as a documented follow-up
# for the final review to triage, rather than two designs drifting apart
# mid-task.
# ---------------------------------------------------------------------------


def _problem_list(message: str) -> list[dict[str, str]]:
    """Every semicolon-joined problem in a refusal message, as its own entry.

    `auctions.settle` collects **every** problem a settlement grid has and
    joins them with `"; "` into one message (`auctions._grid_problems`,
    `auctions.settle`'s own raise), because the console shows a grid and
    fixing one problem at a time is miserable. Splitting that message back
    apart is what lets `POST /api/auctions/{id}/settle` answer the way
    `POST /api/offers` already does -- `{detail, refused: [...]}` -- so the
    console can mark every offending row, not just the first one named in a
    sentence.

    Every other auction transition raises `AuctionRefused` for exactly one
    reason, with no `"; "` in it, so splitting it here still produces a
    `refused` list -- of one entry, itself the whole message -- rather than
    two different response shapes depending on which endpoint asked. None of
    `_grid_problems`'s own problem texts contain `"; "` themselves (see that
    function): a hammer price, a lot number or a fee label never gets a
    semicolon woven into it, so this split never fires in the middle of one
    problem's own words.
    """
    return [{"reason": part} for part in message.split("; ")]


def _refused(_request: Request, exc: Exception) -> JSONResponse:
    """A genuine conflict from `sales_writes` or `auctions`: 409.

    Registered for `sales_writes.SaleRefused` and `auctions.AuctionRefused`
    -- see this module's own note above on why registration, not `except`
    order, is what keeps this from ever catching the narrower sibling each
    one has.
    """
    message = str(exc)
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": message, "refused": _problem_list(message)},
    )


def _bad_input(_request: Request, exc: Exception) -> JSONResponse:
    """Malformed input from `sales_writes` or `auctions`: 422.

    Registered for `sales_writes.SaleInputInvalid` and
    `auctions.SettlementInputInvalid`, the narrow half of each pair `_refused`
    handles the wide half of.
    """
    message = str(exc)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": message, "refused": _problem_list(message)},
    )


def _server_misconfigured(_request: Request, exc: Exception) -> JSONResponse:
    """A precondition the operator must fix, not a bug in one request: 500.

    `auctions.consign` and `sales_venues.ensure_store_venue`/`store_venue_id`
    each raise a bare `RuntimeError` naming exactly what is missing and the
    command that fixes it -- a migrated-but-unseeded database, the real state
    this project's own live database is in today. With no handler registered
    for it, that message reached only the server log: Starlette's default
    handler for an exception nothing catches answers a bare, bodyless 500,
    and an operator staring at the console learned nothing actionable from
    "Internal Server Error". This handler is what makes the message reach
    them instead -- still a 500, because this is a server-side precondition
    and not something the request itself got wrong, but with the same
    `{"detail": ...}` shape every other refusal in this API answers with.
    """
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": str(exc)},
    )


app.add_exception_handler(sales_writes.SaleInputInvalid, _bad_input)
app.add_exception_handler(sales_writes.SaleRefused, _refused)
app.add_exception_handler(auctions.SettlementInputInvalid, _bad_input)
app.add_exception_handler(auctions.AuctionRefused, _refused)
app.add_exception_handler(RuntimeError, _server_misconfigured)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    """Liveness probe.

    Deliberately touches nothing, so it stays honest about the process rather
    than about the database.
    """
    return {"status": "ok"}
