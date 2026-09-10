# Owner console: separating the shop from the back office

Design. Status: approved, not yet implemented (2026-09-09).

Enabling work for the receiving page. It comes first because it decides where
that page is born; building it inside the store application and moving it
afterwards costs the move twice.

| | Depends on |
|---|---|
| **1. Owner console separation** (this document) | -- |
| 2. Receiving purchases | 1 |

## The problem

The frontend is one Vite application. Owner pages -- `Inventory`,
`AdminCoins`, `AdminPeople` -- are routes in the same router as the shop,
guarded at render time by `RequireAuth adminOnly`. Two things follow, and both
are wrong.

Every anonymous visitor to the shop downloads the owner code. It is in the same
bundle; nothing gates a JavaScript chunk on a role.

Worse, the guard *announces the pages it guards*. A visitor who types
`/admin/people` gets "Administrator privileges are required for this page",
which is a confirmation that the page exists. The correct answer to a stranger
asking whether a door exists is not "yes, and it is locked".

## What this is and is not

This is a **frontend structure** change. It is not the access control.
`require_admin` in `backend/app/deps.py` is the access control, it already
guards every administrative endpoint, and nothing here weakens or replaces it.
Separating the bundles is defence in depth plus the removal of an information
leak.

Stated plainly so nobody is surprised later: after this change, someone who
*guesses* `/owner` still reaches a sign-in screen, so the console's existence
remains discoverable to a determined guesser. What this fixes is accidental
discovery from inside the shop. Closing the remaining gap means serving the
console on an interface the public cannot reach, which is a deployment
decision, is recorded under **Later** below, and stacks on this design without
rework.

No backend change is in scope.

## Decision

**One Vite project, two entry points.** Rollup treats each HTML entry as an
independent module graph, so the shop bundle contains no owner module -- not
lazily loaded, not present.

```
frontend/
  index.html              -> /src/store/main.jsx      served at /
  owner.html              -> /src/owner/main.jsx      served at /owner
  vite.config.js          build.rollupOptions.input = { store, owner }
  src/
    shared/   api.js  format.js  auth.jsx  auth-context.js
              reference.jsx  reference-context.js
              LoginForm.jsx  shared.css
    store/    main.jsx  StoreApp.jsx  styles.css
              cart.jsx  cart-context.js
              pages/  Catalog  CoinDetail  Cart  Orders  Login  Register
    owner/    main.jsx  OwnerApp.jsx  styles.css
              pages/  Login  Inventory  AdminCoins  AdminPeople
                      inventory/  (the six existing components)
    test/     helpers.jsx  setup.js
```

Everything stays under `frontend/src`, which means the surrounding toolchain
does not move: `sonar-project.properties` still points `sonar.sources` at
`frontend/src`, vitest's `include: ['src/**/*.{js,jsx}']` still matches, and
`scripts\ccweb_check.cmd` still runs one eslint and one vitest. The split costs
a configuration block, not a second toolchain.

### Alternatives rejected

**Two Vite projects** (`frontend/` and `owner/`, each with its own
`package.json`). Stronger isolation, but two dependency trees to keep in step,
two lint configurations, two test runs, and shared code needs a workspace
package or duplication. The extra isolation is ceremony when there is one
backend and one developer.

**One bundle, lazy-loaded owner subtree.** Cheapest, and it does keep owner
code out of the shop's initial download -- but the chunk is still served on
request and the routes still exist in the shop's router. It addresses the
symptom rather than the structure.

## What moves where

Taken from reading every page's imports rather than from assumption.

| Module | Goes to | Why |
|---|---|---|
| `format.js` | shared | imported by both sides |
| `api.js` | **split** | see below |
| `auth.jsx`, `auth-context.js` | shared | `Login`, `Register`, `Orders` and `AdminPeople` all use it |
| `reference.jsx`, `reference-context.js` | shared | `Catalog` uses it, and so do `AdminCoins` and `ItemEditForm` |
| `cart.jsx`, `cart-context.js` | store | only `Catalog`, `CoinDetail` and `Cart` |
| `pages/inventory/*` (six files) | owner | already a self-contained subtree |

### The API client splits too

`api` is a single object literal, so nothing tree-shakes out of it: every
endpoint written in it is downloaded by every anonymous visitor to the shop.
Leaving it whole would have shipped `/api/inventory/{id}/split`,
`/api/inventory/bulk`, `/api/users/{id}/password` and `/api/customers` to the
storefront — a list of endpoint paths is a better map of the owner's tooling
than the class names the stylesheet split removed, and removed for the same
reason.

So `shared/api.js` keeps the transport (`send`, token handling, `ApiError`)
and the calls the shop and shared components make; `owner/api.js` holds the
console's, spreading the shared object so a console page imports one `api` and
never has to know which half a method came from.

One exception, recorded rather than hidden: `addReferenceValue` stays in
`shared/api.js`. It is called by `ReferenceSelect` in `shared/reference.jsx`,
and shared code may not import from `owner/`. Only console pages render that
component today, so a single write endpoint is visible to the shop. Moving
`ReferenceSelect` into `owner/` would close it, and is not worth doing until
something else needs that move.

### Sessions are shared, deliberately

`api.js` keeps a JWT in `localStorage` under a single key and sends it as a
bearer token. Both bundles are same-origin, so a session obtained in either is
a session in both. Nothing needs to change.

The consequence is that the console needs **its own `/owner/login` route**
rather than redirecting to the shop's. The form itself is one shared component,
`shared/LoginForm.jsx`, that each application wraps in its own page; only the
surrounding layout differs. The two applications must never link to each other:
a link from the console to the shop is harmless, but a link the other way
reintroduces exactly the discovery this design removes.

### The admin guard moves to the router root

`App.jsx` today repeats `<RequireAuth adminOnly>` on four routes. In the
console it wraps the whole router once. Repetition is fine at four routes and
is the pattern that leaks at page five -- a new page is unguarded by *omission*,
which is the failure that does not announce itself. Wrapping once makes the
guard a property of the application rather than a thing to remember.

### The stylesheet splits three ways

`src/styles.css` is 586 lines and 74 selectors. It divides into `shared.css`
(layout, forms, buttons), `store/styles.css`, and `owner/styles.css`. The
owner-only rules are already identifiable by name: `.admin-form`,
`.inventory-table`, `.bulk-bar`, `.review-pane`, `.filter-grid`.

This is not tidiness. A shared stylesheet ships the shop a list of class names
that enumerate the owner's features, which gives back the information the
bundle split just removed.

## Routing and serving

Each bundle is an independent single-page application, so each needs its own
history fallback. This is the one real wrinkle.

The console's router mounts at a basename:

```jsx
// src/owner/main.jsx
<BrowserRouter basename="/owner">
```

In development, Vite with two entries serves `/owner.html`, and its SPA
fallback assumes a single entry. `vite.config.js` therefore gains a
`configureServer` middleware: requests under `/owner` rewrite to `/owner.html`,
and every other non-asset request falls back to `/index.html`. Without it,
`/owner/receiving` works until someone reloads the page or restores a bookmark,
which is the class of bug that survives all of development and appears in use.

**Production serving is out of scope.** The backend serves no static files
today and this document does not invent a deployment. What it records is the
contract any host must satisfy:

- `/owner` and `/owner/*` serve `owner.html`
- everything else serves `index.html`

One rule in nginx, Caddy, or a FastAPI `StaticFiles` mount, written when there
is a deployment to write it for.

## How the guarantee is verified

"The shop bundle contains no owner code" is a claim, and a claim is checked in
a different channel from the one that produced it. Two layers, which fail for
different reasons.

**Source level, inside the existing lint gate.** ESLint `no-restricted-imports`
boundary rules:

- files under `src/store/` may not import any path matching `**/owner/**`
- files under `src/owner/` may not import any path matching `**/store/**`
- files under `src/shared/` may not import either

The rules are written as glob patterns rather than as literal relative paths,
because a page nested one level deeper reaches its sibling tree by `../../`
rather than `../` and a literal rule would silently stop matching.

A static import by path is the only way owner code can enter the shop's graph,
so this catches the cause at edit time, costs nothing, and rides a gate that
already runs.

**Output level, structural.** A script walks the chunk graph reachable from
each entry and fails if any chunk it reaches *contains* a module from the
other application's tree. This inspects the artefact rather than the source,
so it proves the lint rules *achieve* bundle separation rather than merely
describing an intention.

It reads a bundle graph emitted from a Rollup `generateBundle` hook, **not**
Vite's `manifest.json`. As built, this was tried against the manifest first
and was wrong: a manifest records a chunk's imports but never its contents,
and the two entries share a chunk whose manifest record is `"src": null`.
Rollup hoists any module imported by *both* entries into that shared chunk, so
an owner page imported from shop code lands somewhere the manifest attributes
to no tree at all, and the check reports success while the shop downloads it.
The check must therefore read chunk membership. It also follows **dynamic**
imports, which is the one route the lint rules cannot cover: they match a
plain string literal, and a computed specifier such as
``import(`../owner/pages/${name}.jsx`)`` is not one.

It is symmetric — shop-reaches-owner and owner-reaches-shop — which is a
strict superset of the one direction originally specified here. The lint rules
are already symmetric and the second direction costs nothing.

Deliberately not a string search of the built JavaScript: minification renames
identifiers, so a grep can pass for the wrong reason, and a check that passes
for the wrong reason is worse than no check.

The two are not redundant. The lint rule catches a developer's import; the
manifest check catches a build-configuration mistake, such as both entries
sharing a chunk. Either alone leaves a hole.

### Tests

- The console's root guard: anonymous redirects to sign-in, a signed-in
  non-admin is refused, an admin renders.
- The shop returns "not found" for `/inventory/coins` and `/admin/people` --
  asserting the absence of the confirmation message, not merely the absence of
  the page.
- The 69 existing frontend tests continue to pass. Only their import paths
  change; no assertion in them should need rewriting, and one that does is a
  signal that something moved which should not have.

## Migration sequence

The risk in this work is not the design. It is that roughly twenty source files
and fourteen test files move at once. The order below keeps the existing suite
as a safety net at every step rather than only at the end.

1. Create the three trees, move the shared modules, rewrite imports. Scripted,
   with anchors asserted before writing, per `CLAUDE.md`. **The suite must pass
   unchanged** -- nothing has changed but paths.
2. Split `App.jsx` into `StoreApp.jsx` and `OwnerApp.jsx`; move the admin guard
   to the console's router root. Suite passes.
3. Add `owner.html`, `src/owner/main.jsx`, and the basename.
4. `vite.config.js`: two inputs, manifest on, dev middleware.
5. Add the ESLint boundary rules. They should pass immediately. If they do not,
   step 1 was incomplete -- which is the point of putting them here rather than
   first.
6. Split the stylesheet three ways.
7. Add the manifest-check script.
8. Add the new tests: console root guard, shop 404s.

Steps 1 and 2 are where a mistake would hide, and both are covered by the
existing suite. Step 5 doubles as an audit of step 1.

## Later

**Serving the console where the public cannot reach it.** Binding it to
loopback or the local network is the same structural approach
`docs/specs/self-hosted-sonarqube-design.md` takes for SonarQube, which binds
`127.0.0.1` rather than `0.0.0.0` so it is unreachable from the LAN even if the
firewall is wrong. That closes the residual discovery gap described at the top.
It needs a deployment story that does not exist yet, and it requires no change
to anything designed here.

**The receiving page** is the next document. It lands in `src/owner/pages/`
and is the first page written into this structure rather than moved into it.
