# Code quality

One command runs every gate:

```cmd
scripts\ccweb_check.cmd          check only
scripts\ccweb_check.cmd fix      reformat and auto-fix first, then check
```

It exits non-zero if anything fails. Gate on that exit code; do not pipe the
output through a filter that replaces it. Every gate is at zero findings, so
any finding is new -- there is no backlog to read past.

From Git Bash, run it as `cmd //c scripts\\ccweb_check.cmd`: a single `/c` is
rewritten into a path and cmd exits 0 having run nothing. The gate is also a
machine-wide mutex in practice -- never run two at once, since both pytest
runs use the same `ccwebdb_test` database.

---

## What runs

In order:

| Gate | Tool |
|---|---|
| Python formatting | `ruff format --check` |
| Python linting | `ruff check` |
| Mutation-scaffolding guard | `findstr` over `backend\app\*.py` |
| Python types | `mypy` |
| Python tests | `pytest` |
| Frontend linting | `eslint` |
| Frontend formatting | `prettier --check` |
| Frontend tests | `vitest` |
| Frontend bundle isolation | `vite build`, then `frontend/scripts/check-bundle-isolation.mjs` |

If `frontend\node_modules` is missing, the frontend gates are reported as
**not installed** and the run fails: an unrun check is not a passed one.

**One tool owns layout, another owns correctness.** `ruff format` decides line
breaks and `ruff check` has no opinions about them; on the frontend,
`eslint-config-prettier` switches off every stylistic eslint rule for the same
reason.

---

## Nothing is ignored to spare the codebase work

The ruff rule set is broad: pycodestyle, pyflakes, import order, pyupgrade,
bugbear, comprehensions, simplification, return hygiene, pathlib preference,
and **`D` (every public class, method and function carries a docstring)** and
**`ANN` (every function is annotated)**.

Three exceptions exist (`pyproject.toml`), each because the rule does not
describe the code:

**`D203` and `D213` are mutually exclusive with rules that are enabled.**
`D203` wants a blank line before a class docstring and `D211` none; `D213`
wants the summary on the second line and `D212` on the first. One of each pair
must be chosen.

**Tests are exempt from `D103` only.** A pytest function is invoked by the
framework and imported by nothing, so "undocumented *public* function" does
not describe it; its name is the summary. Everything else applies to tests as
to application code, and a test with a reason worth recording has a real
docstring.

**Routers are exempt from `B008`.** FastAPI declares parameters by calling
`File()`, `Form()` and `Depends()` in the default. B008 is right in general
and wrong for this framework.

---

## Types

`mypy` fails the build like any other gate.

### Two traps

**`ReferenceMixin` is a plain mixin, so a checker cannot see the declarative
attributes.** It is always combined with `Base`, but `type[ReferenceMixin]`
does not say so, and `__tablename__`, `__table__` and the keyword constructor
read as missing. A `TYPE_CHECKING` block in `models/base.py` mirrors
`DeclarativeBase`'s own declarations. It must *match* them, not improve on
them: annotating `__tablename__` as `ClassVar[str]` instead of `Any` -- the
obvious improvement -- is an override conflict on every concrete table. It
must also stay under `TYPE_CHECKING`; at runtime an `__init__` there would
precede `Base` in the MRO and shadow the declarative constructor.

**`__mapper_args__` is a `@declared_attr.directive`, not a dict literal.**
As a literal it is caught in a pincer: `RUF012` wants `ClassVar` on a mutable
class attribute, mypy rejects `ClassVar` for the reason above, and `Final` is
"cannot override writable attribute with a final one". A directive is not an
attribute assignment, and matches how `__table_args__` is written. The
`StaleDataError` tests in `test_concurrent_writes.py` prove it still fires:
that exception cannot be raised unless `version_id_col` is configured.

### Where a type is genuinely two things

`seeding.py` walks `SEEDABLE`: every classifier plus `Composition`, which has
`__tablename__` and `__table__` but not `ReferenceMixin`'s `code` and `label`.
Python has no intersection type, so `SeedableModel` is a union, and where the
seeder needs a column only one of them has it reads `__table__.columns`,
putting the runtime guard and its use in the same place.

---

## Frontend bundle isolation

One source tree builds two applications, the shop (`src/store/`) and the owner
console (`src/management/`); shared code lives in `src/shared/`. Neither
application may ship the other's code. That is checked on two channels,
because each catches what the other cannot:

1. **The source, by `eslint`.** `no-restricted-imports` catches a static
   `import ... from '../management/...'` in shop code or the reverse;
   `no-restricted-syntax` on `ImportExpression` catches a dynamic `import()`,
   which `no-restricted-imports` does not inspect.
2. **The built artefact, by `check-bundle-isolation.mjs`.** A Rollup plugin
   (`bundleGraph()` in `vite.config.js`) emits `dist/.vite/bundle-graph.json`
   from `generateBundle`, the one point where Rollup exposes each chunk's
   actual module membership. The check walks every chunk reachable from each
   entry, following dynamic imports too (a computed specifier such as
   ``import(`../management/pages/${name}.jsx`)`` is invisible to eslint), and fails
   if any contains a module from the other application's tree. It is
   symmetric.

It does **not** use Vite's `manifest.json` or grep the built JavaScript. The
manifest never lists a chunk's modules, so it cannot say whether the chunk
shared by both entries holds an console module -- exactly the case to catch.
Minification renames identifiers, so a text search can pass for the wrong
reason. The gate deletes `dist` before building so a failed build cannot leave
a stale graph for the check to read.

---

## Mutation-scaffolding guard

The gate fails if `backend\app\*.py` contains `if False:`, `if True:` or the
text `MUTATION` (case-sensitive), found with `findstr /S /N`.

Mutation testing -- disabling a guard to confirm its test goes red, then
restoring it -- is how guarantees are tested here, and a mutation left behind
is a disabled guard in production. The guard catches the two literal forms
directly. A mutation with no `if` to negate, such as deleting an `AdminUser`
dependency from an endpoint, leaves nothing to grep for, so **the convention
is to leave a `MUTATION` comment at the site while any such mutation is in
place**. This stage then catches the marker. A marker-less mutation of that
shape, or an equivalent form such as `if 0:`, is not detectable here; that is
a known limit, not an oversight to close with more `findstr` patterns.

---

## Fast Refresh

`react-refresh/only-export-components` fires when a module exports a
component *and* something that is not one. Vite then cannot hot-swap an edit
to that file and falls back to a full reload, losing the app's state.

Each context is therefore split in two: a `-context.js` module holds the
context and its hook, and a `.jsx` module holds only the provider --
`shared/auth-context.js` and `shared/auth.jsx`, `shared/reference-context.js`
and `shared/reference.jsx`, `store/cart-context.js` and `store/cart.jsx`. The
`-context` modules import nothing but React, so there are no cycles.
