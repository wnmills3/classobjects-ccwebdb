# Code quality

One command runs every gate:

```
scripts\ccweb_check.cmd          check only
scripts\ccweb_check.cmd fix      reformat and auto-fix first, then check
```

It exits non-zero if anything fails, so CI can call it directly.

---

## What runs

| Gate | Tool | Enforced |
|---|---|---|
| Python formatting | `ruff format` | yes |
| Python linting | `ruff check` | yes |
| Python types | `mypy` | yes |
| Tests | `pytest` | yes |
| Frontend linting | `eslint` | yes (errors) |
| Frontend formatting | `prettier` | yes |
| Frontend bundle isolation | custom | yes |

**One tool owns layout, another owns correctness.** `ruff format` decides line
breaks and `ruff check` is told not to have opinions about them; on the
frontend, `eslint-config-prettier` switches off every stylistic eslint rule for
the same reason. Two tools arguing about a blank line is worse than either
alone.

---

## Nothing is ignored to spare the codebase work

The rule set is deliberately broad: pycodestyle, pyflakes, import order,
pyupgrade, bugbear, comprehensions, simplification, return hygiene, pathlib
preference, and — because they were asked for specifically — **`D` (every
public thing carries a docstring)** and **`ANN` (every function is
annotated)**.

Three exceptions exist, each for a reason that is not "the code failed":

**`D203` and `D213` are mutually exclusive with rules that are enabled.**
`D203` wants a blank line before a class docstring and `D211` wants none;
`D213` wants the summary on the second line and `D212` on the first. Ruff
cannot satisfy both members of either pair. One of each must be chosen.

**Tests are exempt from `D103` only.** A pytest function is invoked by the
framework and imported by nothing, so "undocumented *public* function" does not
describe it — its name is the summary, and
`"""Test that limit is bounded."""` above `test_limit_is_bounded` is noise that
makes the file harder to read. Everything else applies to tests exactly as to
application code: they are annotated, import-sorted, formatted, and where a
test has a reason worth recording it has a real docstring.

**Routers are exempt from `B008`.** FastAPI declares parameters by calling
`File()`, `Form()` and `Depends()` in the default. B008 is right in general and
wrong for this framework.

---

## Types

`mypy` fails the build. It was reported-only for as long as there was a
standing backlog — with forty-odd findings on every run, a new one is
invisible. The backlog is now zero, so a single new finding is the signal and
is treated like any other failing gate.

Most of the backlog was friction between strict typing and SQLAlchemy's
generics rather than defects: `ColumnElement[bool]` where a
`BinaryExpression[bool]` was declared, a `Row` used as though it were a
`tuple` (it becomes one only through `.tuples()`), containers whose element
type could not be inferred from the first thing appended to them.

The ones that *were* defects are the argument for the gate: a self-referential
relationship carrying a meaningless `remote_side=lambda: None`, a return
annotation gone stale after its type changed, several `Session | None` values
used without narrowing, and a cache declared `dict[..., int]` that stored
`None` for a deliberate miss.

### Two traps worth not rediscovering

**`ReferenceMixin` is a plain mixin, so a checker cannot see the declarative
attributes.** It is always combined with `Base`, but nothing in
`type[ReferenceMixin]` says so, and `__tablename__`, `__table__` and the
keyword constructor all read as missing. The fix is a `TYPE_CHECKING` block in
`models/base.py` that mirrors `DeclarativeBase`'s own declarations. It must
*match* them, not improve on them: writing `__tablename__: ClassVar[str]`
rather than `Any` — the obvious improvement, since it is a string — is an
override conflict on all thirty-seven concrete tables and cost 29 findings the
first time it was tried. It must also stay under `TYPE_CHECKING`; at runtime an
`__init__` there would precede `Base` in the MRO and shadow the declarative
constructor.

**`__mapper_args__` as a dict literal is caught in a pincer.** `RUF012` wants
`ClassVar` on a mutable class attribute; mypy rejects `ClassVar` for the reason
above; `Final` escapes `RUF012` but is "cannot override writable attribute with
a final one". A `@declared_attr.directive` is none of the three — it is not an
attribute assignment at all — and it matches how `__table_args__` is already
written. The tests that prove it still works are the `StaleDataError` ones in
`test_concurrent_writes.py`: that exception cannot be raised unless
`version_id_col` is configured, so they fail loudly if the directive stops
firing.

### Where a type is genuinely two things

`seeding.py` walks `SEEDABLE`, which holds every classifier plus `Composition`.
Composition shares the shape the seeder needs — `__tablename__`, `__table__` —
but not `ReferenceMixin`'s `code` and `label` columns, and neither class is a
supertype of the other. Python has no intersection type, so `SeedableModel`
names both as a union. Where the seeder needs a column that only one of them
has, it reads `__table__.columns` rather than the class attribute, which puts
the runtime guard and its use in the same place.

---

## What the linters found that mattered

Worth recording, because it is the argument for having them:

- **Two undefined names.** `ItemImage` and `Listing` were referenced as
  forward-reference strings in `models/core.py` with no `TYPE_CHECKING` import.
  They resolved at runtime through SQLAlchemy's registry, so nothing ever
  failed — but they were invisible to every type checker, and the same file's
  neighbours already did it correctly.
- **A stale-response race, three times over.** `eslint`'s
  `set-state-in-effect` rule pointed at four data-fetching effects. Fixing them
  properly meant deriving "loading" from state rather than storing it, and
  adding a cancellation guard — without which a slow response for an earlier
  search can land after a faster later one and overwrite newer results with
  older ones. That was a real bug in the inventory search, reachable by typing
  quickly.
- **A dead variable and a vestigial `inspect()` call** in the seeding exporter.
- **`Image.LANCZOS`**, which moved to `Image.Resampling.LANCZOS` in Pillow 10
  and survived only because the old name still resolves.

## Frontend bundle isolation

The project builds two applications from one source tree, the public shop
(`src/store/`) and the owner console (`src/owner/`), and neither is supposed
to ship the other's code. That guarantee is checked twice, on two different
channels, because each catches a different kind of mistake:

1. **`eslint`'s `no-restricted-imports` and `no-restricted-syntax` check the
   source.** They fire the moment a developer writes `import ... from
   '../owner/...'` in shop code (or the reverse), including a dynamic
   `import()` — `no-restricted-imports` only inspects static import/export
   declarations, so the dynamic form needed a second, `no-restricted-syntax`,
   rule matching `ImportExpression`.
2. **`frontend/scripts/check-bundle-isolation.mjs` checks the built
   artefact.** It catches what the source-level rules cannot: a
   build-configuration mistake, or any other route a module takes into the
   wrong bundle that isn't a literal import statement in the tree being
   linted.

The second check does **not** read Vite's `manifest.json`, and does not grep
the built JavaScript. Both were tried and both are unsound for this project:

- **The manifest.** A manifest entry records a chunk's own `src` only when
  that chunk belongs to exactly one entry. Building this project's two
  entries produces a chunk shared between them — Rollup puts any module
  imported by *both* `store` and `owner` into it — and the manifest records
  that chunk as `"isEntry": false, "src": null`. It never lists the modules a
  chunk contains, so there is no manifest field that can say whether the
  shared chunk holds an owner-only module. A manifest-based check reports
  success in exactly the case it exists to catch: an owner module hoisted
  into the chunk the shop already downloads.
- **Grepping the built JavaScript.** Rollup minifies identifiers, so a text
  search for a component or module name can pass because the build renamed
  the very thing being searched for. A check that can pass for the wrong
  reason is worse than no check.

Instead, a small Rollup plugin (`bundleGraph()` in `vite.config.js`) listens
to `generateBundle` — the one point where Rollup exposes each chunk's actual
module membership — and emits `dist/.vite/bundle-graph.json`: for every
chunk, its name, whether it is an entry, its static and dynamic imports, and
the source module ids it contains. `check-bundle-isolation.mjs` walks the
chunk graph reachable from each entry, *following dynamic imports too* — the
route `eslint` cannot see, because its rules match a plain string literal and
a computed specifier such as ``import(`../owner/pages/${name}.jsx`)`` is not
one — and inspects the modules inside every chunk
it reaches — including shared chunks — for a path under the other
application's tree. It is symmetric (shop-reaches-owner and
owner-reaches-shop), a strict superset of what was asked for, since the lint
rules are already symmetric and the second direction costs nothing.

Both channels have been mutation-tested: a static `import` of an owner page
from shop code, and a dynamic `import()` of the same page, each made both
`eslint` and the bundle-isolation check fail, and both checks pass again once
reverted.

## Fast Refresh

`react-refresh/only-export-components` fires when a module exports a component
*and* something that is not one. It matters because the bundler can then no
longer tell whether an edit to that file is a component change it can hot-swap,
so it falls back to a full reload and loses whatever state the app was holding.

Each context is therefore split in two: `auth-context.js` holds the context and
the `useAuth` hook, `auth.jsx` holds only `AuthProvider`. Same for cart and
reference. The `-context` modules import nothing but React, so there are no
cycles.
