# Code quality

**What this covers.** The quality gates every change must pass -- Python
formatting, linting, types and tests; frontend linting, formatting, tests and
bundle isolation; and a guard against leftover mutation-test scaffolding --
with the configuration behind each and the few deliberate exceptions.

**Who it is for.** Anyone about to commit, and anyone tempted to silence a
finding rather than fix it.

**Why it matters.** Every gate is at zero findings, so any finding is new and
real -- there is no backlog to read past. That only stays true if nothing is
ignored to spare the codebase work, and if the gate's exit code, not a skim
of its output, decides.

One command runs everything:

```cmd
.\scripts\ccweb_check.cmd          check only
.\scripts\ccweb_check.cmd fix      ruff format, ruff check --fix and prettier --write first, then check
```

It exits non-zero if anything fails, and prints `FAILED:` with the names of
the failed stages. Gate on that exit code; do not pipe the output through a
filter that replaces it.

From Git Bash, see the `cmd //c` form in
[environment-setup.md](environment-setup.md) (*Gotchas*): a single `/c` runs
nothing and exits 0. Never run two gates at once: both pytest runs use the
same `ccwebdb_test` database.

---

## What runs

In order:

| Stage | Tool | Configured in |
|---|---|---|
| Python formatting | `ruff format --check` | `pyproject.toml` `[tool.ruff]`, `[tool.ruff.format]` |
| Python linting | `ruff check` | `pyproject.toml` `[tool.ruff.lint]` |
| Mutation-scaffolding guard | `findstr /S /N` over `backend\app\*.py` | `scripts\ccweb_check.cmd` |
| Python types | `mypy` | `pyproject.toml` `[tool.mypy]` |
| Python tests | `pytest -q` (from `backend`) | `pyproject.toml` `[tool.pytest.ini_options]` |
| Frontend linting | `eslint --max-warnings 0` | `frontend/eslint.config.js` |
| Frontend formatting | `prettier --check` | `frontend/.prettierrc.json` |
| Frontend tests | `vitest run` | `frontend/vite.config.js` |
| Frontend bundle isolation | `vite build`, then `frontend/scripts/check-bundle-isolation.mjs` | `frontend/vite.config.js` (`bundleGraph()`) |

Python tools run from the `ccwebdb` conda environment and Node tools from
`frontend\node_modules`, both invoked by path. If
`frontend\node_modules\eslint` is missing, the frontend stages are reported as
**not installed** and the run fails: an unrun check is not a passed one.

`eslint` fails on any warning as well as any error (`--max-warnings 0`, in the
gate and in `npm run lint` alike), so a warning is never left to accumulate.

**One tool owns layout, another owns correctness.** `ruff format` decides line
breaks and `ruff check` has no opinions about them; on the frontend,
`eslint-config-prettier` goes last in the eslint config and switches off every
stylistic rule. Both formatters use an 88-column line.

**Markdown is not reformatted.** `ruff format` excludes `docs/**/*.md`: specs
quote code *fragments*, indented to show where they go, and formatting them as
modules dedents and reflows them into different code.

**Tests fail on warnings that signal bugs.** pytest runs with
`--strict-markers` and turns every `DeprecationWarning` (bar one named anyio
alias) and SQLAlchemy's "cartesian product" warning into errors; the latter almost
always means two aliases of one table were mixed in a query, silently
doubling every sum. The two registered markers, `claim_invariant_waiver` and
`auction_invariant_waiver`, each need a `reason=` and are the only opt-outs
from the suite-wide invariants in `backend/tests/conftest.py`.

---

## Nothing is ignored to spare the codebase work

The ruff rule set is broad: pycodestyle (`E`, `W`), pyflakes (`F`), import
order (`I`), pyupgrade (`UP`), bugbear (`B`), simplification (`SIM`),
comprehensions (`C4`), ruff's own (`RUF`), pathlib preference (`PTH`), return
hygiene (`RET`), and **`D` (every public class, method and function carries a
docstring, Google convention)** and **`ANN` (every function is annotated)**.

Three rule exceptions exist (`pyproject.toml`), each because the rule does not
describe the code, plus Alembic's generated files:

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

**Alembic's generated files are not linted as project code.**
`backend/alembic/versions` is excluded from ruff and lies outside mypy's
`files`: a revision is written from Alembic's template, so checking it
checks the template. `backend/alembic/env.py`, also generated, is exempt
from `D`, `ANN` and `I`.

`backend/app` carries no inline `# noqa` or `# type: ignore`. The tests carry
a few `# type: ignore[<code>]`, each scoped to one error code, and mypy's
`warn_unused_ignores` fails any that stops being needed.

---

## Types

`mypy` fails the build like any other gate. It checks `backend/app` and
`backend/tests` with the `pydantic.mypy` plugin, and requires every function
to be fully annotated (`disallow_untyped_defs`, `disallow_incomplete_defs`),
alongside `check_untyped_defs`, `strict_equality`, `no_implicit_optional` and
the warnings for redundant casts and unused ignores.

### Two traps

**`ReferenceMixin` is a plain mixin, so a checker cannot see the declarative
attributes.** It is always combined with `Base`, but `type[ReferenceMixin]`
does not say so, and `__tablename__`, `__table__` and the keyword constructor
read as missing. A `TYPE_CHECKING` block in `models/base.py` mirrors
`DeclarativeBase`'s own declarations. It must *match* them, not improve on
them: annotating `__tablename__` as `ClassVar[str]` instead of `Any` -- the
obvious improvement -- is an override conflict on every concrete classifier
table. It must also stay under `TYPE_CHECKING`; at runtime an `__init__` there
would precede `Base` in the MRO and shadow the declarative constructor.

**`__mapper_args__` is a `@declared_attr.directive`, not a dict literal.**
Six models set it, to make `version` their `version_id_col`
(`inventory_item`, `listing`, `sales_venue`, `sales_lot`, `sales_order`,
`auction`). As a literal it is caught in a pincer: `RUF012` wants `ClassVar`
on a mutable class attribute, mypy rejects `ClassVar` for the reason above,
and `Final` is "cannot override writable attribute with a final one". A
directive is not an attribute assignment, and matches how `__table_args__` is
written. `test_a_second_writer_is_refused_rather_than_silently_winning`
(`test_concurrent_writes.py`) proves it still fires: it expects a
`StaleDataError`, which cannot be raised unless `version_id_col` is
configured.

### Where a type is genuinely two things

`seeding.py` walks `SEEDABLE`: every classifier plus `Composition`, which has
`__tablename__` and `__table__` but not `ReferenceMixin`'s `code` and `label`.
Python has no intersection type, so `SeedableModel` is a union, and where the
seeder needs a column only one of them has it reads `__table__.columns`,
putting the runtime guard and its use in the same place.

---

## Frontend bundle isolation

One source tree builds two applications, the shop (`src/store/`, entry
`index.html`) and the management console (`src/management/`, entry
`management.html`); shared code lives in `src/shared/`. Neither application
may ship the other's code, and shared code may depend on neither. That is
checked on two channels, because each catches what the other cannot:

1. **The source, by `eslint`.** `no-restricted-imports` catches a static
   `import ... from '../management/...'` in shop code, the reverse, and either
   from `src/shared/`; `no-restricted-syntax` on `ImportExpression` catches a
   dynamic `import()` with a literal specifier, which `no-restricted-imports`
   does not inspect.
2. **The built artefact, by `check-bundle-isolation.mjs`.** A Rollup plugin
   (`bundleGraph()` in `vite.config.js`) emits `dist/.vite/bundle-graph.json`
   from `generateBundle`, the one point where Rollup exposes each chunk's
   actual module membership. The check walks every chunk reachable from each
   entry, following dynamic imports too (a computed specifier such as
   ``import(`../management/pages/${name}.jsx`)`` is invisible to eslint), and
   fails if any contains a module from the other application's tree. It is
   symmetric.

It does **not** use Vite's `manifest.json` or grep the built JavaScript. The
manifest never lists a chunk's modules, so it cannot say whether the chunk
shared by both entries holds a console module -- exactly the case to catch.
Minification renames identifiers, so a text search can pass for the wrong
reason. The gate deletes `dist` before building so a failed build cannot leave
a stale graph for the check to read.

---

## Mutation-scaffolding guard

The gate fails if any `.py` file under `backend\app` contains `if False:`,
`if True:` or the text `MUTATION` (case-sensitive), found with
`findstr /S /N`.

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
to that file and falls back to a full reload, losing the app's state. The
rule is a warning, which `--max-warnings 0` makes fatal.

Each context is therefore split in two: a `-context.js` module holds the
context and its hook, and `.jsx` modules export only components, the
provider among them -- `shared/auth-context.js` and `shared/auth.jsx`,
`shared/reference-context.js` and `shared/reference.jsx`,
`store/cart-context.js` and `store/cart.jsx`, `management/help-context.js` and
`management/HelpBar.jsx`. The `-context`
modules import nothing but React, so there are no cycles. Constants and
helpers that pages and their tests share go in a plain `.js` module beside
them for the same reason (`management/pages/listing-labels.js`,
`store/pages/lot-entry.js`).
