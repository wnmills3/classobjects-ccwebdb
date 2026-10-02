"""Errors that mean the database is missing reference data it needs.

`ReferenceDataMissing` is what a migrated-but-unseeded database raises when a
writer needs a row from a closed vocabulary that `app.seeding.seed_all` (or,
for the one platform the migration itself does not create on a database built
from the models, `app.sales_venues.ensure_store_venue`) is what seeds it.
That state is real and expected -- the migrations build the schema and the
seed load is a separate step -- so it is told to the person who hit it, not
left to look like an ordinary crash.

**A narrow subclass of `RuntimeError`, not `RuntimeError` itself, is what
`app.main` registers a handler for.**
`RuntimeError` is also the base of `NotImplementedError` and
`RecursionError`, and of every incidental `RuntimeError` a library or a
generator's teardown can raise anywhere in the app, including on the shop's
anonymous routes. A handler on the base class would catch all of those too:
it would hand `str(exc)` verbatim to whoever asked, and because
`ExceptionMiddleware` handles the exception, it would never reach
`ServerErrorMiddleware` -- no traceback logged, no re-raise in `TestClient`
-- so a genuine bug that happened to be a `RuntimeError` would be a tidy
JSON 500 labelled a server precondition. A class that means exactly one
thing avoids that: its three raise sites (`app.auctions.consign`,
`app.sales_venues.ensure_store_venue`, `app.sales_venues.store_venue_id`)
answer the operator with a message, and everything else crashes loudly, the
way a bug should.
"""

from __future__ import annotations

__all__ = ["ReferenceDataMissing"]


class ReferenceDataMissing(RuntimeError):
    """A seeded vocabulary row or platform this write needs is not there yet.

    Always carries the fix along with the fact: which command to run, or
    which migration to apply. Raised only by writers that know the missing
    row is a real, recoverable database state -- never as a stand-in for "I
    do not know what went wrong."
    """
