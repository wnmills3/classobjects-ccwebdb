"""Errors that mean the database is missing reference data it needs.

`ReferenceDataMissing` is what a migrated-but-unseeded database raises when a
writer needs a row from a closed vocabulary that `app.seeding.seed_all` (or,
for the one platform the migration itself does not create on a database built
from the models, `app.sales_venues.ensure_store_venue`) is what seeds it.
That state is real and expected -- migration `e267ec3aedc1`'s own docstring
says so, and the live database is in it today -- so it is told to the person
who hit it, not left to look like an ordinary crash.

**A narrow subclass of `RuntimeError`, not `RuntimeError` itself, is what
`app.main` registers a handler for** (ruling R24, Task 5 fix round 1).
`RuntimeError` is also the base of `NotImplementedError` and
`RecursionError`, and of every incidental `RuntimeError` a library or a
generator's teardown can raise anywhere in the app, including on the shop's
anonymous routes. Handling the base class caught all of those too: it handed
`str(exc)` verbatim to whoever asked, and because `ExceptionMiddleware` had
already handled the exception, it never reached `ServerErrorMiddleware` --
tracebacks stopped being logged and `TestClient` stopped re-raising, so a
genuine bug that happened to be a `RuntimeError` became a tidy JSON 500
wearing a "server precondition" label instead of the crash it was. A class
that means exactly one thing closes that gap without giving anything up: the
same three raise sites (`app.auctions.consign`,
`app.sales_venues.ensure_store_venue`, `app.sales_venues.store_venue_id`)
still answer the operator with the same message, and everything else still
crashes loudly, the way a bug should.
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
