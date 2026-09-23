"""FastAPI application entry point."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import auctions, sales_writes
from .config import settings
from .errors import ReferenceDataMissing
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

_log = logging.getLogger(__name__)

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
# behind this module's own registration for `sales_writes`' identical pair.
#
# Ruling R23 (Task 5 follow-up): `routers/offers.py`'s `record_listing_sale`
# used to carry its own ordered `except SaleInputInvalid` / `except
# SaleRefused` clauses -- a live production path where reversing the two
# would have silently turned every 422 into a 409, with mypy accepting the
# reversal cleanly either way. That endpoint now lets both propagate to the
# handlers registered here instead, the same as every `app.auctions` caller
# already did; the hazard is unwritable there too, not merely documented.
# ---------------------------------------------------------------------------


def _refusal_body(exc: Exception) -> dict[str, object]:
    """The `{detail, refused: [...]}` body every refusal in this API answers with.

    Ruling R21 (Task 5 follow-up): built from the raised exception's own
    `refusals` attribute when it has one -- `auctions.AuctionRefused` and its
    narrower `SettlementInputInvalid` both do, one `auctions.AuctionRefusal`
    per problem `auctions.settle` found, `lot_number` included where the
    problem named one. This **replaces** splitting `str(exc)` on `"; "`,
    which was a text convention standing in for this structure: it broke
    silently the moment any one problem's own words held a semicolon, and
    nothing type-checked it. `str(exc)` itself -- `detail` here -- is
    untouched; only where `refused` comes from has changed.

    `sales_writes.SaleRefused` and its narrower `SaleInputInvalid` carry no
    `refusals` attribute -- `record_sale_lines` stops at the first problem
    rather than collecting a grid's worth -- so `getattr` falls back to a
    `refused` list of exactly one entry, built from the plain message. That
    fallback is exact, not an approximation: a single-reason refusal *is*
    one entry, whichever class raised it.
    """
    message = str(exc)
    refusals = getattr(exc, "refusals", None)
    if refusals is None:
        return {"detail": message, "refused": [{"reason": message, "lot_number": None}]}
    return {
        "detail": message,
        "refused": [
            {"reason": refusal.reason, "lot_number": refusal.lot_number}
            for refusal in refusals
        ],
    }


def _refused(_request: Request, exc: Exception) -> JSONResponse:
    """A genuine conflict from `sales_writes` or `auctions`: 409.

    Registered for `sales_writes.SaleRefused` and `auctions.AuctionRefused`
    -- see this module's own note above on why registration, not `except`
    order, is what keeps this from ever catching the narrower sibling each
    one has.
    """
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT, content=_refusal_body(exc)
    )


def _bad_input(_request: Request, exc: Exception) -> JSONResponse:
    """Malformed input from `sales_writes` or `auctions`: 422.

    Registered for `sales_writes.SaleInputInvalid` and
    `auctions.SettlementInputInvalid`, the narrow half of each pair `_refused`
    handles the wide half of.
    """
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=_refusal_body(exc)
    )


def _server_misconfigured(request: Request, exc: Exception) -> JSONResponse:
    """A precondition the operator must fix, not a bug in one request: 500.

    `auctions.consign` and `sales_venues.ensure_store_venue`/`store_venue_id`
    each raise `errors.ReferenceDataMissing`, naming exactly what is missing
    and the command that fixes it -- a migrated-but-unseeded database, the
    real state this project's own live database is in today. With no handler
    registered for it, that message reached only the server log: Starlette's
    default handler for an exception nothing catches answers a bare, bodyless
    500, and an operator staring at the console learned nothing actionable
    from "Internal Server Error". This handler is what makes the message
    reach them instead -- still a 500, because this is a server-side
    precondition and not something the request itself got wrong, but with the
    same `{"detail": ...}` shape every other refusal in this API answers
    with.

    **Registered on `errors.ReferenceDataMissing`, never on `RuntimeError`
    itself** (ruling R24, Task 5 fix round 1, reverting an earlier version of
    this handler that was). `RuntimeError` is also the base of
    `NotImplementedError` and `RecursionError`, and of every incidental
    `RuntimeError` anywhere in the app, shop routes included -- handling the
    base class handed a stranger's internal message to an anonymous caller,
    and because `ExceptionMiddleware` had already handled the exception, it
    never reached `ServerErrorMiddleware`: no logged traceback, and
    `TestClient` stopped re-raising it, so a genuine bug that happened to be
    a `RuntimeError` became a tidy, misleadingly-labelled 500 instead of the
    crash it was. `test_an_unrelated_runtime_error_is_not_swallowed`
    (`test_auctions_api.py`) is the regression test: a plain `RuntimeError`
    from a monkeypatched writer must still escape `TestClient` unhandled.

    **Only an administrator's request is told what is missing** (auctions
    whole-branch review, Minor #6). `sales_venues.store_venue_id` raises this
    from the shop's public routes too, and "run `alembic upgrade head` on
    this database" is an operator's instruction, not a stranger's business.
    `deps.require_admin` marks an admin request; everyone else gets a
    generic message, and the full one goes to the log either way, so the
    operator still finds it.
    """
    _log.error("server misconfigured: %s", exc)
    if getattr(request.state, "is_admin", False):
        detail = str(exc)
    else:
        detail = "The shop is not fully set up yet. Please try again later."
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": detail},
    )


def _field_conflicts(_request: Request, exc: Exception) -> JSONResponse:
    """Render `FieldConflicts` as a 409 with the per-field list beside the sentence."""
    assert isinstance(exc, inventory.FieldConflicts)
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": exc.detail, "conflicts": exc.conflicts},
    )


app.add_exception_handler(inventory.FieldConflicts, _field_conflicts)
app.add_exception_handler(sales_writes.SaleInputInvalid, _bad_input)
app.add_exception_handler(sales_writes.SaleRefused, _refused)
app.add_exception_handler(auctions.SettlementInputInvalid, _bad_input)
app.add_exception_handler(auctions.AuctionRefused, _refused)
app.add_exception_handler(ReferenceDataMissing, _server_misconfigured)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    """Liveness probe.

    Deliberately touches nothing, so it stays honest about the process rather
    than about the database.
    """
    return {"status": "ok"}
