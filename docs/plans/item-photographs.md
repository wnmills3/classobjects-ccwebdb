# Photographs on an item Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an item gain a photograph at any time, and import the ~673
existing photographs by reading the item code out of their filenames.

**Architecture:** A pure filename parser and a CLI pass import the library;
`image_links.py` becomes the only writer of `item_image` and owns the primary
swap; four new endpoints let the console list, attach, re-role and detach; a
`PhotosPanel` in the item editor and an `/owner/photos` page carry the
judgement the pass cannot.

**Tech Stack:** FastAPI + SQLAlchemy 2, Pillow (already in use via
`app.imaging`), React 19 + Vite, pytest, vitest + Testing Library, ruff, mypy,
eslint, prettier.

**Spec:** `docs/specs/item-photographs-design.md`

## Global Constraints

- **No migration.** `image`, `image_derivative`, `item_image` and `image_role`
  already exist with every column this needs. If a task seems to need one,
  stop and report BLOCKED.
- **`image_links.py` is the only writer of `item_image`** once Task 4 lands.
  Nothing else may insert, update or delete that table — the same rule
  `offering_writes` and `lifecycle_writes` hold for theirs.
- **Originals are never served.** Public requests are answered only from
  `image_derivative` rows. Do not add a route that serves `Image.storage_key`.
- **Metadata is stripped at ingest and that is not negotiable.** Do not store
  GPS, and do not add EXIF fields beyond the `captured_at` already kept.
- **Console API calls go in `frontend/src/owner/api.js`, never
  `frontend/src/shared/api.js`** — every anonymous shop visitor downloads the
  shared bundle, and a build check enforces it.
- **Docstrings and annotations are enforced at zero findings**: ruff `D` and
  `ANN`. `backend/tests/*` is exempt from `D103` only — test functions still
  need `-> None` and annotated parameters. Line length 88.
- **eslint and prettier at zero findings.** Prettier: no semicolons, single
  quotes, print width 88, trailing commas.
- **The gate is `scripts\ccweb_check.cmd`** (`fix` auto-fixes first). Run it as
  its own command and read the exit code separately — a trailing `; echo $?`
  reports the echo, not the gate. Never pipe it.
- **cmd/batch only. No PowerShell**, no `.ps1`, no `powershell -Command` from
  inside a `.cmd`. `NoDefaultCurrentDirectoryInExePath=1` is set, so invoke
  scripts as `.\script.cmd` or by full path.
- **One pytest session at a time.** Run pytest from the repository root;
  `pyproject.toml` sets `testpaths`, `pythonpath` and `addopts = "-q
  --strict-markers"` — do not add another `-q`.
- **Use Write for new files, Edit for surgical changes. No shell heredocs.**
- Commit on `feat/item-photographs`. **Never commit to `main`; do not merge and
  do not push** — the owner does that.
- **Never run the pass against the real photograph library.** All pass testing
  uses temporary directories. The first real run is a dry run the owner
  watches.

---

## File Structure

**Backend, created:**

- `backend/app/image_store.py` — `ingest`, moved out of the router. The router
  and the pass both call it.
- `backend/app/photo_names.py` — the pure filename parser. One responsibility,
  no imports from the app beyond typing.
- `backend/app/image_links.py` — the only writer of `item_image`; owns attach,
  re-role, set-primary (with the swap) and detach.
- `backend/app/photo_import.py` — the CLI pass.
- `backend/app/routers/image_links.py` — `PATCH`/`DELETE /api/image-links/{id}`.

**Backend, modified:**

- `backend/app/config.py` — `photo_library_root`.
- `backend/app/routers/images.py` — imports `ingest`; gains
  `GET /api/images` and `POST /api/images/{image_id}/links`.
- `backend/app/schemas.py` — link request/response shapes.
- `backend/app/main.py` — register the new router.
- `backend/app/models/images.py` — correct `ItemImage`'s docstring.
- `.gitignore` — `photos/`.

**Backend, created (tests):**

- `backend/tests/test_photo_names.py`
- `backend/tests/test_image_links.py`
- `backend/tests/test_photo_import.py`

**Frontend, created:**

- `frontend/src/owner/pages/inventory/PhotosPanel.jsx` (+ test)
- `frontend/src/owner/pages/Photos.jsx` (+ test)

**Frontend, modified:**

- `frontend/src/owner/api.js`
- `frontend/src/owner/pages/inventory/ItemEditForm.jsx`
- `frontend/src/owner/OwnerApp.jsx`

---

### Task 1: Move `ingest` out of the router

**Files:**
- Create: `backend/app/image_store.py`
- Modify: `backend/app/routers/images.py`
- Test: `backend/tests/test_images.py` (existing; must keep passing unchanged)

**Interfaces:**
- Produces: `image_store.ingest(db: Session, raw: bytes, source_ref: str | None) -> Image`.
  It raises `imaging.ImageRejected` rather than `HTTPException` — the router
  translates. Tasks 8 and 6 both call it.

**Why:** a CLI pass importing a router is backwards, and `ingest` is not an
HTTP concern. This task changes no behaviour.

- [ ] **Step 1: Create the module**

Write `backend/app/image_store.py`:

```python
"""Storing a photograph: cleanse, put the bytes, record the row.

Lifted out of `routers.images` because a CLI pass importing a router is
backwards. What is left in the router is the HTTP: reading an upload,
translating a refusal into a 422, serving renditions.

`ImageRejected` propagates rather than becoming an `HTTPException` here. The
router turns it into a 422 for a caller holding a request; `app.photo_import`
catches it and names the file it came from, which is the whole point of
separating them.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .imaging import cleanse, derivative_key, make_derivative, original_key
from .models import DerivativeKind, Image, ImageDerivative
from .storage import get_storage

__all__ = ["DERIVATIVE_SIZES", "ingest"]

#: Longest edge per rendition. Both are generated at ingest rather than on
#: demand: a catalogue page asks for dozens of thumbnails at once, and
#: resizing on request turns one page view into dozens of decodes.
DERIVATIVE_SIZES: dict[DerivativeKind, int] = {
    DerivativeKind.thumb: settings.thumbnail_max_px,
    DerivativeKind.web: settings.web_max_px,
}


def ingest(db: Session, raw: bytes, source_ref: str | None) -> Image:
    """Cleanse, store and record one file. Idempotent by content.

    Raises `imaging.ImageRejected` if the bytes are not something we are
    willing to store.
    """
    cleansed = cleanse(raw)

    existing = db.scalar(select(Image).where(Image.sha256 == cleansed.sha256))
    if existing is not None:
        return existing

    storage = get_storage()
    key = original_key(cleansed.sha256, cleansed.media_type)
    storage.put(key, cleansed.data)

    image = Image(
        sha256=cleansed.sha256,
        storage_key=key,
        media_type=cleansed.media_type,
        byte_size=len(cleansed.data),
        width=cleansed.width,
        height=cleansed.height,
        captured_at=cleansed.captured_at,
        source_ref=source_ref,
    )
    db.add(image)
    db.flush()

    for kind, longest_edge in DERIVATIVE_SIZES.items():
        data, width, height, media_type = make_derivative(cleansed.data, longest_edge)
        derived_key = derivative_key(cleansed.sha256, kind.value, media_type)
        storage.put(derived_key, data)
        db.add(
            ImageDerivative(
                image_id=image.id,
                kind=kind,
                storage_key=derived_key,
                width=width,
                height=height,
            )
        )

    db.flush()
    return image
```

- [ ] **Step 2: Point the router at it**

In `backend/app/routers/images.py`, delete the local `ingest` function and the
local `DERIVATIVE_SIZES`, and import them:

```python
from ..image_store import DERIVATIVE_SIZES, ingest
```

In `upload_image`, wrap the call so the refusal still becomes a 422:

```python
    try:
        image = ingest(db, raw, source_ref=file.filename)
    except ImageRejected as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
```

Remove now-unused imports from the router (`cleanse`, `original_key`,
`make_derivative`, `derivative_key`, `ImageDerivative`, and `select` if nothing
else uses it). Keep `ImageRejected`. Ruff will name any you miss.

- [ ] **Step 3: Run the images suite unchanged**

Run: `python -m pytest backend/tests/test_images.py -v`
Expected: PASS, no test edits. Behaviour is identical; only the module boundary
moved. If a test fails, the move changed something it should not have.

- [ ] **Step 4: Run the gate and commit**

Run: `scripts\ccweb_check.cmd fix`
Expected: exit code 0

```bash
git add backend/app/image_store.py backend/app/routers/images.py
git commit -m "Put storing a photograph where a pass can reach it"
```

---

### Task 2: The photograph library setting

**Files:**
- Modify: `backend/app/config.py`
- Modify: `.gitignore`
- Test: `backend/tests/test_config.py`

**Interfaces:**
- Produces: `settings.photo_library_root: Path`, defaulting to
  `REPO_ROOT / "photos"`, overridable by `PHOTO_LIBRARY_ROOT`. Task 8 reads it.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_config.py`:

```python
def test_the_photograph_library_defaults_beside_the_repository() -> None:
    from app.config import REPO_ROOT, Settings

    assert Settings().photo_library_root == REPO_ROOT / "photos"


def test_the_photograph_library_can_be_moved_by_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import Settings

    monkeypatch.setenv("PHOTO_LIBRARY_ROOT", "/tmp/elsewhere")
    assert Settings().photo_library_root == Path("/tmp/elsewhere")
```

Add `from pathlib import Path` and `import pytest` to that file's imports if
they are not already there. Read the file first — it may already construct
`Settings()` in a helper you should reuse.

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest backend/tests/test_config.py -v`
Expected: FAIL, `AttributeError` / `ValidationError` — no such field.

- [ ] **Step 3: Add the setting**

In `backend/app/config.py`, below the image-storage block:

```python
    # --- photograph library -------------------------------------------------
    # Where `python -m app.photo_import` looks for files to import. A
    # directory rather than an upload, because the photographs are already on
    # the machine and 673 of them through a file picker is a long sitting.
    # Git-ignored: a coin collection's photographs are not source code.
    photo_library_root: Path = REPO_ROOT / "photos"
```

- [ ] **Step 4: Ignore the directory**

Append to `.gitignore`:

```
# Photographs waiting to be imported (app.photo_import). Not source.
/photos/
```

- [ ] **Step 5: Run to verify it passes, then gate and commit**

Run: `python -m pytest backend/tests/test_config.py -v`
Expected: PASS

Run: `scripts\ccweb_check.cmd fix`
Expected: exit code 0

```bash
git add backend/app/config.py .gitignore backend/tests/test_config.py
git commit -m "Say where the photographs waiting to be imported live"
```

---

### Task 3: The filename parser

**Files:**
- Create: `backend/app/photo_names.py`
- Test: `backend/tests/test_photo_names.py` (create)

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class ParsedName:
      item_code: str
      sequence: int
      role: str        # "obverse" | "reverse" | "unassigned"
      is_primary: bool

  def parse(filename: str) -> ParsedName | None
  ```
  `None` means the name does not follow the convention. Task 8 consumes it.

**Why pure:** a convention rots quietly. One function with a table of cases
puts every rule where it can be read at once, and lets the pass be tested
without touching it.

- [ ] **Step 1: Write the failing table test**

Write `backend/tests/test_photo_names.py`:

```python
"""The filename convention, case by case.

`<item_code>_<nn>.<ext>` -- CC-000412_01.jpg. A table rather than prose,
because a convention is only as good as the cases nobody remembered.
"""

from __future__ import annotations

import pytest
from app.photo_names import ParsedName, parse


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        (
            "CC-000412_01.jpg",
            ParsedName("CC-000412", 1, "obverse", True),
        ),
        (
            "CC-000412_02.jpg",
            ParsedName("CC-000412", 2, "reverse", False),
        ),
        (
            "CC-000412_03.jpg",
            ParsedName("CC-000412", 3, "unassigned", False),
        ),
        (
            "CC-000412_17.jpeg",
            ParsedName("CC-000412", 17, "unassigned", False),
        ),
        # A path, not a bare name: the pass walks a directory tree.
        (
            "box3/CC-000001_01.png",
            ParsedName("CC-000001", 1, "obverse", True),
        ),
    ],
)
def test_names_that_follow_the_convention(
    filename: str, expected: ParsedName
) -> None:
    assert parse(filename) == expected


@pytest.mark.parametrize(
    "filename",
    [
        "cc-000412_01.jpg",  # lowercase prefix is a miss, not a correction
        "CC-00412_01.jpg",  # five digits
        "CC-0004123_01.jpg",  # seven digits
        "CC-000412.jpg",  # no sequence
        "CC-000412_1.jpg",  # sequence not zero-padded
        "CC-000412_00.jpg",  # sequence must start at 1
        "CC-000412-01.jpg",  # hyphen, not underscore
        "IMG_4021.jpg",  # a camera's own name
        "CC-000412_01",  # no extension
        "",
    ],
)
def test_names_that_do_not(filename: str) -> None:
    assert parse(filename) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest backend/tests/test_photo_names.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.photo_names'`

- [ ] **Step 3: Write the parser**

Write `backend/app/photo_names.py`:

```python
"""Reading the item out of a photograph's filename.

`<item_code>_<nn>.<ext>` -- `CC-000412_01.jpg`. The convention is applied by
whoever takes the photographs; this reads it and never rewrites it.

**Nothing here repairs input.** A lowercase `cc-` is a miss, not a correction:
a parser that quietly fixes a filename teaches the operator that the
convention does not matter, and the next mistake is one nobody catches.

The sequence carries the role as well as the order, because the two common
shots are the two sides and photographing them front-then-back is what people
already do. Everything past the second is `unassigned`, to be set by hand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

__all__ = ["ParsedName", "parse"]

#: `item_code` is generated as `'CC-' || lpad(nextval(...), 6, '0')`, so the
#: shape is exact rather than a guess. Anchored at both ends: a name with
#: anything else around the code is not this convention.
_NAME = re.compile(r"^(CC-\d{6})_(\d{2,})\.[^.]+$")

#: What each position means. Beyond the second, a person decides.
_ROLES = {1: "obverse", 2: "reverse"}
_DEFAULT_ROLE = "unassigned"


@dataclass(frozen=True)
class ParsedName:
    """What a conforming filename says about the photograph."""

    item_code: str
    sequence: int
    #: An `image_role` code: obverse, reverse, or unassigned.
    role: str
    #: The first photograph is the one the shop shows.
    is_primary: bool


def parse(filename: str) -> ParsedName | None:
    """What the name says, or `None` if it does not follow the convention.

    Accepts a path as well as a bare name -- the pass walks a tree, and the
    directories above a photograph carry no meaning here.
    """
    # Both separators, because the library is read on Windows and the tests
    # build paths with either.
    name = PureWindowsPath(PurePosixPath(filename).name).name
    match = _NAME.match(name)
    if match is None:
        return None
    sequence = int(match.group(2))
    if sequence < 1:
        return None
    return ParsedName(
        item_code=match.group(1),
        sequence=sequence,
        role=_ROLES.get(sequence, _DEFAULT_ROLE),
        is_primary=sequence == 1,
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest backend/tests/test_photo_names.py -v`
Expected: PASS, 15 passed

- [ ] **Step 5: Run the gate and commit**

Run: `scripts\ccweb_check.cmd fix`
Expected: exit code 0

```bash
git add backend/app/photo_names.py backend/tests/test_photo_names.py
git commit -m "Read the item code out of a photograph's filename"
```

---

### Task 4: `image_links.py`, the only writer of `item_image`

**Files:**
- Create: `backend/app/image_links.py`
- Test: `backend/tests/test_image_links.py` (create)

**Interfaces:**
- Produces:
  ```python
  class LinkRefused(Exception): ...

  def attach(db, *, image, item, role: str | None, is_primary: bool, sort_order: int = 0) -> ItemImage
  def set_role(db, link: ItemImage, role: str | None) -> None
  def make_primary(db, link: ItemImage) -> None
  def detach(db, link: ItemImage) -> None
  ```
  Tasks 6, 7 and 8 all call these. No other module writes `item_image`.

**The primary swap is the reason this module exists.**
`uq_item_image_primary` is a partial unique index — one primary per item — so
setting a new primary must clear the old one first, in the same transaction,
or the write is rejected outright.

- [ ] **Step 1: Write the failing tests**

Write `backend/tests/test_image_links.py`:

```python
"""Linking a photograph to an item, and the one-primary rule.

`app.image_links` is the only writer of `item_image`.
"""

from __future__ import annotations

import pytest
from app import image_links
from app.models import Image, InventoryItem, ItemImage
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.conftest import build_item


def _image(db: Session, sha: str) -> Image:
    image = Image(
        sha256=sha,
        storage_key=f"orig/{sha}.jpg",
        media_type="image/jpeg",
        byte_size=10,
    )
    db.add(image)
    db.flush()
    return image


def test_attaching_records_the_role_and_the_order(db: Session) -> None:
    item = build_item(db)
    link = image_links.attach(
        db,
        image=_image(db, "a" * 64),
        item=item,
        role="obverse",
        is_primary=True,
        sort_order=1,
    )
    db.flush()
    assert link.inventory_item_id == item.id
    assert link.is_primary is True
    assert link.sort_order == 1


def test_a_second_primary_replaces_the_first(db: Session) -> None:
    item = build_item(db)
    first = image_links.attach(
        db, image=_image(db, "b" * 64), item=item, role="obverse", is_primary=True
    )
    second = image_links.attach(
        db, image=_image(db, "c" * 64), item=item, role="reverse", is_primary=False
    )
    db.flush()

    image_links.make_primary(db, second)
    db.flush()

    db.refresh(first)
    db.refresh(second)
    assert first.is_primary is False
    assert second.is_primary is True
    # The index allows exactly one; prove only one is set.
    primaries = db.scalars(
        select(ItemImage).where(
            ItemImage.inventory_item_id == item.id, ItemImage.is_primary
        )
    ).all()
    assert len(primaries) == 1


def test_attaching_as_primary_also_replaces(db: Session) -> None:
    item = build_item(db)
    first = image_links.attach(
        db, image=_image(db, "d" * 64), item=item, role="obverse", is_primary=True
    )
    db.flush()
    image_links.attach(
        db, image=_image(db, "e" * 64), item=item, role="detail", is_primary=True
    )
    db.flush()
    db.refresh(first)
    assert first.is_primary is False


def test_detaching_leaves_the_photograph(db: Session) -> None:
    item = build_item(db)
    image = _image(db, "f" * 64)
    link = image_links.attach(
        db, image=image, item=item, role=None, is_primary=False
    )
    db.flush()

    image_links.detach(db, link)
    db.flush()

    assert db.get(ItemImage, link.id) is None
    assert db.get(Image, image.id) is not None


def test_the_same_photograph_cannot_be_attached_twice(db: Session) -> None:
    item = build_item(db)
    image = _image(db, "0" * 64)
    image_links.attach(db, image=image, item=item, role=None, is_primary=False)
    db.flush()
    with pytest.raises(image_links.LinkRefused):
        image_links.attach(db, image=image, item=item, role=None, is_primary=False)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest backend/tests/test_image_links.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.image_links'`

- [ ] **Step 3: Write the module**

Write `backend/app/image_links.py`:

```python
"""Linking a photograph to an item -- the only writer of `item_image`.

The same rule `app.offering_writes` holds for listing status and
`app.lifecycle_writes` for item status: one module writes this table, so that
what a link means cannot drift between the console and the import pass.

**The primary swap is why this is a module and not four lines in a router.**
`uq_item_image_primary` is a partial unique index -- at most one primary per
item -- so promoting a photograph means demoting the incumbent first, inside
the same transaction. Two callers doing that independently is two chances to
get the order wrong, and the failure is a rejected write at commit time, far
from whoever caused it.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import Image, InventoryItem, ItemImage
from .references import code_to_id
from .models import ImageRole

__all__ = ["LinkRefused", "attach", "detach", "make_primary", "set_role"]


class LinkRefused(Exception):
    """This photograph cannot be linked to this item."""


def _clear_primary(db: Session, item_id: int, keep: int | None = None) -> None:
    """Demote whatever is primary for this item, except `keep`.

    Runs before a promotion, never after: the partial unique index refuses a
    second primary, so the order is the whole trick.
    """
    stmt = update(ItemImage).where(
        ItemImage.inventory_item_id == item_id, ItemImage.is_primary
    )
    if keep is not None:
        stmt = stmt.where(ItemImage.id != keep)
    db.execute(stmt.values(is_primary=False))
    db.flush()


def attach(
    db: Session,
    *,
    image: Image,
    item: InventoryItem,
    role: str | None,
    is_primary: bool,
    sort_order: int = 0,
) -> ItemImage:
    """Link `image` to `item`. Raises `LinkRefused` if it is already linked."""
    existing = db.scalar(
        select(ItemImage).where(
            ItemImage.inventory_item_id == item.id,
            ItemImage.image_id == image.id,
        )
    )
    if existing is not None:
        raise LinkRefused(
            f"{item.item_code} already has this photograph (link #{existing.id})."
        )

    if is_primary:
        _clear_primary(db, item.id)

    link = ItemImage(
        inventory_item_id=item.id,
        image_id=image.id,
        image_role_id=code_to_id(db, ImageRole, role, "image_role"),
        is_primary=is_primary,
        sort_order=sort_order,
    )
    db.add(link)
    db.flush()
    return link


def set_role(db: Session, link: ItemImage, role: str | None) -> None:
    """Say what this photograph shows -- obverse, reverse, slab, and so on."""
    link.image_role_id = code_to_id(db, ImageRole, role, "image_role")
    db.flush()


def make_primary(db: Session, link: ItemImage) -> None:
    """Make this the photograph the shop shows, demoting the incumbent."""
    if link.inventory_item_id is None:
        raise LinkRefused("An unattached photograph cannot be an item's primary.")
    _clear_primary(db, link.inventory_item_id, keep=link.id)
    link.is_primary = True
    db.flush()


def detach(db: Session, link: ItemImage) -> None:
    """Unfile the photograph. The image itself is untouched and remains."""
    db.delete(link)
    db.flush()
```

Merge the two `from .models import` lines into one — they are split above only
for readability of the diff, and ruff's import sorting will object.

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest backend/tests/test_image_links.py -v`
Expected: PASS, 5 passed

- [ ] **Step 5: Prove the swap is load-bearing**

This is the mutation the spec asks for. In `image_links.py`, comment out the
`_clear_primary(db, link.inventory_item_id, keep=link.id)` line inside
`make_primary`, then run:

`python -m pytest backend/tests/test_image_links.py::test_a_second_primary_replaces_the_first -v`

Expected: FAIL — `IntegrityError` on `uq_item_image_primary`, because the
index refuses a second primary. That failure is the proof the index is doing
work rather than sitting there. Restore the line, re-run, confirm it passes,
and confirm `git diff` on the file is clean. Record all four steps with output
in your report.

- [ ] **Step 6: Run the gate and commit**

Run: `scripts\ccweb_check.cmd fix`
Expected: exit code 0

```bash
git add backend/app/image_links.py backend/tests/test_image_links.py
git commit -m "Give item_image one writer, and the primary one owner"
```

---

### Task 5: `GET /api/images`

**Files:**
- Modify: `backend/app/routers/images.py`
- Modify: `backend/app/schemas.py`
- Test: `backend/tests/test_images.py`

**Interfaces:**
- Produces: `GET /api/images?inventory_item_id=N` and `GET /api/images?unattached=true`,
  returning `list[ImageLinkOut]`. Tasks 10 and 11 consume it.
- Produces `ImageLinkOut`: the link *and* its image, because the console needs
  both and a second round-trip per thumbnail is a page that loads in pieces.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_images.py`:

```python
def test_an_items_photographs_come_back_in_order(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    from app import image_links

    from tests.conftest import build_item

    item = build_item(db)
    for index, sha in enumerate(("1" * 64, "2" * 64), start=1):
        image = Image(
            sha256=sha,
            storage_key=f"orig/{sha}.jpg",
            media_type="image/jpeg",
            byte_size=10,
        )
        db.add(image)
        db.flush()
        image_links.attach(
            db,
            image=image,
            item=item,
            role=None,
            is_primary=index == 1,
            sort_order=index,
        )
    db.commit()

    listed = client.get(
        f"/api/images?inventory_item_id={item.id}", headers=admin_headers
    )
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert [row["sort_order"] for row in body] == [1, 2]
    assert body[0]["is_primary"] is True
    assert body[0]["thumbnail_url"].endswith("/thumb")


def test_unattached_photographs_can_be_listed(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    image = Image(
        sha256="3" * 64,
        storage_key="orig/3.jpg",
        media_type="image/jpeg",
        byte_size=10,
    )
    db.add(image)
    db.commit()

    listed = client.get("/api/images?unattached=true", headers=admin_headers)
    assert listed.status_code == 200, listed.text
    assert [row["image_id"] for row in listed.json()] == [image.id]


def test_listing_every_photograph_at_once_is_refused(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    assert client.get("/api/images", headers=admin_headers).status_code == 422
```

Check `test_images.py`'s existing imports before adding; `Image`, `Session` and
`TestClient` may already be there.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest backend/tests/test_images.py -k "photographs" -v`
Expected: FAIL — 405 or 404, there is no list route.

- [ ] **Step 3: Add the schema**

In `backend/app/schemas.py`, beside `ImageOut`:

```python
class ImageLinkOut(BaseModel):
    """One photograph as it is filed: the link, and the image it points at.

    Both together because every console surface that lists photographs needs
    both, and a second request per thumbnail is a page that arrives in pieces.
    """

    model_config = ConfigDict(from_attributes=True)

    #: Null for a photograph nobody has filed yet.
    link_id: int | None = None
    inventory_item_id: int | None = None
    item_code: str | None = None
    image_id: int
    image_role: str | None = None
    is_primary: bool = False
    sort_order: int = 0
    captured_at: datetime | None = None
    thumbnail_url: str
    web_url: str
```

- [ ] **Step 4: Add the route**

In `backend/app/routers/images.py`, **above** the existing
`@router.get("/{image_id}/{kind}")` so a literal path is never shadowed by the
parameterised one:

```python
@router.get("", response_model=list[ImageLinkOut])
def list_images(
    db: DbSession,
    _admin: AdminUser,
    inventory_item_id: int | None = None,
    unattached: bool = False,
) -> list[ImageLinkOut]:
    """An item's photographs, or the ones nobody has filed yet.

    One filter is required. An unfiltered list of every photograph in the
    collection is a page nobody asked for and a query that grows without
    bound; making the caller say which set it wants costs one parameter.
    """
    if (inventory_item_id is None) == (not unattached):
        raise HTTPException(
            status_code=422,
            detail="Pass exactly one of inventory_item_id or unattached=true.",
        )

    if unattached:
        linked = select(ItemImage.image_id).where(
            ItemImage.inventory_item_id.is_not(None)
        )
        rows = db.scalars(
            select(Image)
            .where(Image.id.not_in(linked))
            .order_by(Image.captured_at.desc().nullslast(), Image.id)
        ).all()
        return [
            ImageLinkOut(image_id=row.id, captured_at=row.captured_at, **image_urls(row.id))
            for row in rows
        ]

    links = db.scalars(
        select(ItemImage)
        .where(ItemImage.inventory_item_id == inventory_item_id)
        .order_by(ItemImage.sort_order, ItemImage.id)
    ).all()
    return [_link_out(db, link) for link in links]


def _link_out(db: Session, link: ItemImage) -> ImageLinkOut:
    """Project a filed photograph into the API shape."""
    role = db.get(ImageRole, link.image_role_id) if link.image_role_id else None
    return ImageLinkOut(
        link_id=link.id,
        inventory_item_id=link.inventory_item_id,
        item_code=link.item.item_code if link.item else None,
        image_id=link.image_id,
        image_role=role.code if role else None,
        is_primary=link.is_primary,
        sort_order=link.sort_order,
        captured_at=link.image.captured_at,
        **image_urls(link.image_id),
    )
```

Import `ImageLinkOut` from `..schemas`.

- [ ] **Step 5: Run to verify they pass, then gate and commit**

Run: `python -m pytest backend/tests/test_images.py -v`
Expected: PASS

Run: `scripts\ccweb_check.cmd fix`
Expected: exit code 0

```bash
git add backend/app/routers/images.py backend/app/schemas.py backend/tests/test_images.py
git commit -m "Let the console ask which photographs an item has"
```

---

### Task 6: `POST /api/images/{image_id}/links`

**Files:**
- Modify: `backend/app/routers/images.py`, `backend/app/schemas.py`
- Test: `backend/tests/test_image_links.py`

**Interfaces:**
- Consumes: `image_links.attach`, `sale_state.guard`.
- Produces: `POST /api/images/{image_id}/links` taking
  `{inventory_item_id, image_role, is_primary, sort_order, acknowledge_for_sale}`
  and returning `ImageLinkOut`.

**The guard:** attaching changes what a buyer sees, because the shop serves the
primary image. `sale_state.guard(db, [item], acknowledged=...)` raises 409
unless acknowledged — see `docs/specs/for-sale-guards-design.md`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_image_links.py`:

```python
def test_attaching_through_the_api_files_the_photograph(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    item = build_item(db)
    image = _image(db, "7" * 64)
    db.commit()

    made = client.post(
        f"/api/images/{image.id}/links",
        json={
            "inventory_item_id": item.id,
            "image_role": "obverse",
            "is_primary": True,
        },
        headers=admin_headers,
    )
    assert made.status_code == 201, made.text
    assert made.json()["is_primary"] is True
    assert made.json()["item_code"] == item.item_code


def test_attaching_to_a_listed_item_is_refused_until_acknowledged(
    client: TestClient, db: Session, listing: Listing, admin_headers: dict[str, str]
) -> None:
    image = _image(db, "8" * 64)
    db.commit()
    body = {"inventory_item_id": listing.inventory_item_id, "image_role": "obverse"}

    refused = client.post(
        f"/api/images/{image.id}/links", json=body, headers=admin_headers
    )
    assert refused.status_code == 409
    assert "For sale" in refused.json()["detail"]

    made = client.post(
        f"/api/images/{image.id}/links",
        json={**body, "acknowledge_for_sale": True},
        headers=admin_headers,
    )
    assert made.status_code == 201, made.text
```

Add `from app.models import Listing` and `from fastapi.testclient import TestClient`
to that file's imports.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest backend/tests/test_image_links.py -k "through_the_api or listed" -v`
Expected: FAIL, 404 — no such route.

- [ ] **Step 3: Add the request schema**

In `backend/app/schemas.py`:

```python
class ImageLinkIn(BaseModel):
    """Filing a photograph against an item."""

    inventory_item_id: int
    image_role: str | None = None
    is_primary: bool = False
    sort_order: int = 0
    #: Set after a refusal to say the caller knows the item is for sale
    #: (app.sale_state). The shop serves an item's primary photograph.
    acknowledge_for_sale: bool = False
```

- [ ] **Step 4: Add the route**

In `backend/app/routers/images.py`:

```python
@router.post(
    "/{image_id}/links",
    status_code=status.HTTP_201_CREATED,
    response_model=ImageLinkOut,
)
def attach_image(
    image_id: int, payload: ImageLinkIn, db: DbSession, _admin: AdminUser
) -> ImageLinkOut:
    """File a photograph against an item.

    Refused with 409 for an item that is for sale until the caller
    acknowledges it: the shop serves an item's primary photograph, so filing
    one changes what a buyer is looking at.
    """
    image = db.get(Image, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")
    item = db.get(InventoryItem, payload.inventory_item_id)
    if item is None:
        raise HTTPException(
            status_code=404,
            detail=f"No such item: {payload.inventory_item_id}",
        )

    sale_state.guard(db, [item], acknowledged=payload.acknowledge_for_sale)

    try:
        link = image_links.attach(
            db,
            image=image,
            item=item,
            role=payload.image_role,
            is_primary=payload.is_primary,
            sort_order=payload.sort_order,
        )
    except image_links.LinkRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    db.refresh(link)
    return _link_out(db, link)
```

Add `from .. import image_links, sale_state` and import `ImageLinkIn`.

- [ ] **Step 5: Run to verify, gate and commit**

Run: `python -m pytest backend/tests/test_image_links.py -v`
Expected: PASS

Run: `scripts\ccweb_check.cmd fix`
Expected: exit code 0

```bash
git add backend/app/routers/images.py backend/app/schemas.py backend/tests/test_image_links.py
git commit -m "File a photograph against an item over the API"
```

---

### Task 7: `PATCH` and `DELETE /api/image-links/{id}`

**Files:**
- Create: `backend/app/routers/image_links.py`
- Modify: `backend/app/main.py`, `backend/app/schemas.py`
- Test: `backend/tests/test_image_links.py`

**Interfaces:**
- Produces: `PATCH /api/image-links/{link_id}` (role and/or primary) and
  `DELETE /api/image-links/{link_id}` (detach). Both guarded.

**Why a separate prefix:** `/api/images/{image_id}/{kind}` already claims a
two-segment path under `/images` with an integer first segment. Putting link
routes at `/api/images/links/...` would rely on FastAPI failing to parse
`"links"` as an integer — correctness by declaration order. A separate prefix
has no ordering to get wrong.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_image_links.py`:

```python
def test_a_photograph_can_be_re_roled_and_promoted(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    item = build_item(db)
    first = image_links.attach(
        db, image=_image(db, "9" * 64), item=item, role="obverse", is_primary=True
    )
    second = image_links.attach(
        db, image=_image(db, "a1" * 32), item=item, role=None, is_primary=False
    )
    db.commit()

    changed = client.patch(
        f"/api/image-links/{second.id}",
        json={"image_role": "reverse", "is_primary": True},
        headers=admin_headers,
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["image_role"] == "reverse"
    assert changed.json()["is_primary"] is True

    db.expire_all()
    assert db.get(ItemImage, first.id).is_primary is False


def test_detaching_keeps_the_photograph(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    item = build_item(db)
    image = _image(db, "b1" * 32)
    link = image_links.attach(
        db, image=image, item=item, role=None, is_primary=False
    )
    db.commit()

    gone = client.delete(f"/api/image-links/{link.id}", headers=admin_headers)
    assert gone.status_code == 204, gone.text

    db.expire_all()
    assert db.get(ItemImage, link.id) is None
    assert db.get(Image, image.id) is not None


def test_detaching_from_a_listed_item_is_refused_until_acknowledged(
    client: TestClient, db: Session, listing: Listing, admin_headers: dict[str, str]
) -> None:
    item = db.get(InventoryItem, listing.inventory_item_id)
    assert item is not None
    link = image_links.attach(
        db, image=_image(db, "c1" * 32), item=item, role=None, is_primary=True
    )
    db.commit()

    refused = client.delete(f"/api/image-links/{link.id}", headers=admin_headers)
    assert refused.status_code == 409
    assert "For sale" in refused.json()["detail"]

    gone = client.delete(
        f"/api/image-links/{link.id}?acknowledge_for_sale=true", headers=admin_headers
    )
    assert gone.status_code == 204, gone.text
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest backend/tests/test_image_links.py -k "re_roled or detaching" -v`
Expected: FAIL, 404

- [ ] **Step 3: Add the patch schema**

In `backend/app/schemas.py`:

```python
class ImageLinkUpdate(BaseModel):
    """What may change about a filed photograph."""

    image_role: str | None = None
    is_primary: bool | None = None
    #: Set after a refusal (app.sale_state).
    acknowledge_for_sale: bool = False
```

- [ ] **Step 4: Write the router**

Write `backend/app/routers/image_links.py`:

```python
"""Changing how a photograph is filed, and unfiling it.

A separate prefix from `/api/images` on purpose: that router already serves
`/{image_id}/{kind}`, and a literal `links` segment in the same position would
only avoid collision because `"links"` does not parse as an integer.
Correctness by declaration order is a trap.

**Detaching is not deleting.** `DELETE /api/images/{id}` destroys the
photograph and its stored bytes; this removes the link and leaves the
photograph, unattached, to be filed somewhere else. A mis-filed photograph is
a filing error, and a filing error must not be data loss.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status

from .. import image_links, sale_state
from ..deps import AdminUser, DbSession
from ..models import InventoryItem, ItemImage
from ..schemas import ImageLinkOut, ImageLinkUpdate
from .images import _link_out

router = APIRouter(prefix="/image-links", tags=["images"])


def _guarded_link(
    db: DbSession, link_id: int, *, acknowledged: bool
) -> ItemImage:
    """The link, with its item's for-sale warning already answered."""
    link = db.get(ItemImage, link_id)
    if link is None:
        raise HTTPException(status_code=404, detail="Link not found")
    item = (
        db.get(InventoryItem, link.inventory_item_id)
        if link.inventory_item_id is not None
        else None
    )
    sale_state.guard(db, [item] if item else [], acknowledged=acknowledged)
    return link


@router.patch("/{link_id}", response_model=ImageLinkOut)
def update_link(
    link_id: int, payload: ImageLinkUpdate, db: DbSession, _admin: AdminUser
) -> ImageLinkOut:
    """Say what this photograph shows, or make it the one the shop uses."""
    link = _guarded_link(db, link_id, acknowledged=payload.acknowledge_for_sale)
    if payload.image_role is not None:
        image_links.set_role(db, link, payload.image_role)
    if payload.is_primary:
        image_links.make_primary(db, link)
    db.commit()
    db.refresh(link)
    return _link_out(db, link)


@router.delete("/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def detach_link(
    link_id: int,
    db: DbSession,
    _admin: AdminUser,
    acknowledge_for_sale: bool = False,
) -> Response:
    """Unfile the photograph. The image itself remains, unattached."""
    link = _guarded_link(db, link_id, acknowledged=acknowledge_for_sale)
    image_links.detach(db, link)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

`_link_out` is imported from `.images`. If ruff objects to importing a private
name across modules, promote it to `link_out` in `routers/images.py` and update
both call sites — that is the tidier outcome anyway.

- [ ] **Step 5: Register the router**

In `backend/app/main.py`, beside the other `include_router` calls, following
whatever prefix pattern they use:

```python
from .routers import image_links as image_links_router
...
app.include_router(image_links_router.router, prefix=settings.api_prefix)
```

Read the surrounding lines first and match them exactly.

- [ ] **Step 6: Run to verify, gate and commit**

Run: `python -m pytest backend/tests/test_image_links.py -v`
Expected: PASS

Run: `scripts\ccweb_check.cmd fix`
Expected: exit code 0

```bash
git add backend/app/routers/image_links.py backend/app/main.py backend/app/schemas.py backend/tests/test_image_links.py
git commit -m "Re-role, promote and unfile a photograph"
```

---

### Task 8: The import pass

**Files:**
- Create: `backend/app/photo_import.py`
- Test: `backend/tests/test_photo_import.py` (create)

**Interfaces:**
- Consumes: `photo_names.parse`, `image_store.ingest`, `image_links.attach`,
  `settings.photo_library_root`.
- Produces: `python -m app.photo_import [--commit] [--root PATH]`, and
  `run(db, root, *, commit) -> ImportReport` for the tests.

**NEVER run this against the real library.** Tests use `tmp_path`.

- [ ] **Step 1: Write the failing tests**

Write `backend/tests/test_photo_import.py`:

```python
"""Importing a directory of photographs.

Every test builds its own library under `tmp_path`. The real library is never
touched by the suite.
"""

from __future__ import annotations

import io
from pathlib import Path

from app import photo_import
from app.models import Image, ItemImage
from PIL import Image as PILImage
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.conftest import build_item


def _jpeg(colour: tuple[int, int, int] = (10, 20, 30)) -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", (8, 8), colour).save(buffer, format="JPEG")
    return buffer.getvalue()


def _library(root: Path, names: dict[str, bytes]) -> Path:
    for name, data in names.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def test_a_dry_run_writes_nothing(db: Session, tmp_path: Path) -> None:
    item = build_item(db)
    db.commit()
    root = _library(tmp_path, {f"{item.item_code}_01.jpg": _jpeg()})

    report = photo_import.run(db, root, commit=False)

    assert report.linked == 1
    assert db.scalar(select(Image)) is None


def test_commit_files_the_photograph_with_its_role(
    db: Session, tmp_path: Path
) -> None:
    item = build_item(db)
    db.commit()
    root = _library(
        tmp_path,
        {
            f"{item.item_code}_01.jpg": _jpeg((10, 20, 30)),
            f"{item.item_code}_02.jpg": _jpeg((40, 50, 60)),
        },
    )

    report = photo_import.run(db, root, commit=True)

    assert report.linked == 2
    links = db.scalars(
        select(ItemImage)
        .where(ItemImage.inventory_item_id == item.id)
        .order_by(ItemImage.sort_order)
    ).all()
    assert [link.sort_order for link in links] == [1, 2]
    assert links[0].is_primary is True
    assert links[1].is_primary is False


def test_running_twice_changes_nothing(db: Session, tmp_path: Path) -> None:
    item = build_item(db)
    db.commit()
    root = _library(tmp_path, {f"{item.item_code}_01.jpg": _jpeg()})

    photo_import.run(db, root, commit=True)
    second = photo_import.run(db, root, commit=True)

    assert second.linked == 0
    assert second.already == 1
    assert len(db.scalars(select(ItemImage)).all()) == 1


def test_a_name_that_does_not_follow_the_convention_is_kept_unattached(
    db: Session, tmp_path: Path
) -> None:
    root = _library(tmp_path, {"IMG_4021.jpg": _jpeg()})

    report = photo_import.run(db, root, commit=True)

    assert report.linked == 0
    assert [name for name, _ in report.unmatched] == ["IMG_4021.jpg"]
    image = db.scalar(select(Image))
    assert image is not None
    assert db.scalar(select(ItemImage)) is None


def test_an_unknown_item_code_is_kept_unattached(
    db: Session, tmp_path: Path
) -> None:
    root = _library(tmp_path, {"CC-999999_01.jpg": _jpeg()})

    report = photo_import.run(db, root, commit=True)

    assert [name for name, _ in report.unmatched] == ["CC-999999_01.jpg"]
    assert db.scalar(select(Image)) is not None
    assert db.scalar(select(ItemImage)) is None


def test_a_deleted_or_split_item_is_not_linked_to(
    db: Session, tmp_path: Path
) -> None:
    from datetime import UTC, datetime

    deleted = build_item(db)
    deleted.deleted_at = datetime.now(UTC)
    split = build_item(db)
    split.split_at = datetime.now(UTC)
    db.commit()
    root = _library(
        tmp_path,
        {
            f"{deleted.item_code}_01.jpg": _jpeg((7, 7, 7)),
            f"{split.item_code}_01.jpg": _jpeg((8, 8, 8)),
        },
    )

    report = photo_import.run(db, root, commit=True)

    assert report.linked == 0
    assert sorted(name for name, _ in report.unmatched) == sorted(
        [f"{deleted.item_code}_01.jpg", f"{split.item_code}_01.jpg"]
    )
    # Both photographs are still stored -- nothing is ever dropped.
    assert len(db.scalars(select(Image)).all()) == 2
    assert db.scalar(select(ItemImage)) is None


def test_two_files_claiming_one_slot_link_neither(
    db: Session, tmp_path: Path
) -> None:
    item = build_item(db)
    db.commit()
    root = _library(
        tmp_path,
        {
            f"a/{item.item_code}_01.jpg": _jpeg((1, 2, 3)),
            f"b/{item.item_code}_01.jpg": _jpeg((4, 5, 6)),
        },
    )

    report = photo_import.run(db, root, commit=True)

    assert report.linked == 0
    assert len(report.collisions) == 2
    assert db.scalar(select(ItemImage)) is None


def test_an_occupied_sequence_is_never_replaced(
    db: Session, tmp_path: Path
) -> None:
    item = build_item(db)
    db.commit()
    first = _library(tmp_path / "one", {f"{item.item_code}_01.jpg": _jpeg((1, 1, 1))})
    photo_import.run(db, first, commit=True)

    second = _library(tmp_path / "two", {f"{item.item_code}_01.jpg": _jpeg((9, 9, 9))})
    report = photo_import.run(db, second, commit=True)

    assert report.linked == 0
    assert len(report.occupied) == 1
    assert len(db.scalars(select(ItemImage)).all()) == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest backend/tests/test_photo_import.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.photo_import'`

- [ ] **Step 3: Write the pass**

Write `backend/app/photo_import.py`. Follow `app.vendor_cleanup`'s shape: a
module docstring with the command line in it, a `run()` the tests call, and a
`main()` that prints and returns an exit code.

Requirements the tests pin, restated so they are not inferred from the tests
alone:

- `run(db, root, *, commit)` walks `root` recursively, sorted, so a report is
  reproducible.
- Every file is ingested through `image_store.ingest`, whatever its name.
  `ImageRejected` is caught and recorded, not raised.
- `photo_names.parse` decides the link. `None` → `unmatched`.
- An unknown `item_code`, a deleted item (`deleted_at is not None`) or a split
  item (`split_at is not None`) → `unmatched`, with the reason.
- Two files parsing to the same `(item_code, sequence)` → **both** go to
  `collisions` and **neither** is linked.
- A `(item_code, sequence)` already holding a link → `occupied`.
- An image already linked to that item → `already` (the idempotent case).
- `sort_order` is the sequence; `role` and `is_primary` come from `ParsedName`.
- With `commit=False` the function **must not leave rows behind**: do the work
  then `db.rollback()`. With `commit=True`, `db.commit()`.
- The report counts for-sale items among those it would link, using
  `sale_state.for_sale`, and `main()` prints that count. The pass does not
  refuse them — a CLI pass has nobody to acknowledge a warning, and a batch job
  that auto-acknowledges is worse than no guard because it looks safe.

The report and the entry point, exactly:

```python
@dataclass
class ImportReport:
    """What one run of the pass did, or would do."""

    #: Photographs filed against an item by this run.
    linked: int = 0
    #: Already filed against that item -- the idempotent case, not a problem.
    already: int = 0
    #: (filename, reason) for a file that was stored but not filed.
    unmatched: list[tuple[str, str]] = field(default_factory=list)
    #: Filenames that parsed to a slot another file in this run also claimed.
    collisions: list[str] = field(default_factory=list)
    #: (filename, what already holds the slot).
    occupied: list[tuple[str, str]] = field(default_factory=list)
    #: (filename, why) for bytes `app.imaging` refused.
    rejected: list[tuple[str, str]] = field(default_factory=list)
    #: Item codes among those filed that are for sale. Reported, not refused.
    for_sale: list[str] = field(default_factory=list)


def run(db: Session, root: Path, *, commit: bool) -> ImportReport:
    """Import every photograph under `root`. Rolls back unless `commit`."""
```

The ordering inside `run` that the tests pin: parse every filename **first**
and find the slot collisions before anything is written, because a collision
must stop *both* files being linked and you cannot know about the second one
while processing the first. Then walk the files in sorted order, ingest each,
and link the ones that survived.

`main()` takes `--commit` and `--root PATH` (defaulting to
`settings.photo_library_root`), prints one line per exception naming the file,
prints the counts, and ends with `"(dry run -- nothing written; pass
--commit)"` when not committing.

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest backend/tests/test_photo_import.py -v`
Expected: PASS, 8 passed

- [ ] **Step 5: Run the gate and commit**

Run: `scripts\ccweb_check.cmd fix`
Expected: exit code 0

```bash
git add backend/app/photo_import.py backend/tests/test_photo_import.py
git commit -m "Import a library of photographs by reading their filenames"
```

---

### Task 9: The console's API calls

**Files:**
- Modify: `frontend/src/owner/api.js`

**Interfaces:**
- Produces:
  ```js
  listItemImages(inventoryItemId)
  listUnattachedImages()
  attachImage(imageId, { inventoryItemId, imageRole, isPrimary, sortOrder, acknowledgeForSale })
  updateImageLink(linkId, { imageRole, isPrimary, acknowledgeForSale })
  detachImage(linkId, { acknowledgeForSale })
  ```
  Tasks 10 and 11 consume these.

- [ ] **Step 1: Add the calls**

In `frontend/src/owner/api.js`, beside the existing `uploadImage`:

```js
  listItemImages: (inventoryItemId) =>
    send(`/api/images?inventory_item_id=${inventoryItemId}`),
  listUnattachedImages: () => send('/api/images?unattached=true'),
  attachImage: (
    imageId,
    { inventoryItemId, imageRole, isPrimary = false, sortOrder = 0, acknowledgeForSale = false },
  ) =>
    send(`/api/images/${imageId}/links`, {
      method: 'POST',
      body: {
        inventory_item_id: inventoryItemId,
        image_role: imageRole ?? null,
        is_primary: isPrimary,
        sort_order: sortOrder,
        acknowledge_for_sale: acknowledgeForSale,
      },
    }),
  updateImageLink: (linkId, { imageRole, isPrimary, acknowledgeForSale = false }) =>
    send(`/api/image-links/${linkId}`, {
      method: 'PATCH',
      body: {
        image_role: imageRole ?? null,
        is_primary: isPrimary ?? null,
        acknowledge_for_sale: acknowledgeForSale,
      },
    }),
  detachImage: (linkId, { acknowledgeForSale = false } = {}) =>
    send(
      `/api/image-links/${linkId}?acknowledge_for_sale=${acknowledgeForSale}`,
      { method: 'DELETE' },
    ),
```

- [ ] **Step 2: Run the gate and commit**

Run: `scripts\ccweb_check.cmd fix`
Expected: exit code 0

```bash
git add frontend/src/owner/api.js
git commit -m "Give the console the photograph calls it needs"
```

---

### Task 10: `PhotosPanel` in the item editor

**Files:**
- Create: `frontend/src/owner/pages/inventory/PhotosPanel.jsx` (+ `.test.jsx`)
- Modify: `frontend/src/owner/pages/inventory/ItemEditForm.jsx`

**Interfaces:**
- Consumes: the Task 9 calls; `ForSaleNotice` from
  `frontend/src/owner/pages/ForSaleNotice.jsx`, whose props are
  `{ uses, checked, onChange, action, heading, show }`.
- Produces: `<PhotosPanel itemId={number} saleState={array} />`.

**Shape it like `OffersPanel`:** read from the server, never from the editor's
draft; a docstring explaining why it exists and what decision it encodes.

- [ ] **Step 1: Write the failing test**

Write `frontend/src/owner/pages/inventory/PhotosPanel.test.jsx` covering:

- it lists an item's photographs in `sort_order`, marking the primary one
- uploading calls `api.uploadImage` with the item id
- "Make primary" calls `api.updateImageLink` with `isPrimary: true`
- "Remove" calls `api.detachImage` and NOT `api.deleteImage` — detaching is
  not deleting, and this is the assertion that keeps them apart
- with a non-empty `saleState`, the notice renders and the acknowledgement is
  carried on the next write

Mock `../../api` the way `ErrorsPanel.test.jsx` does, and render with
`renderWithProviders(..., { strict: true })`.

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx vitest run src/owner/pages/inventory/PhotosPanel.test.jsx`
Expected: FAIL, cannot resolve `./PhotosPanel`

- [ ] **Step 3: Write the panel**

Thumbnails from `thumbnail_url` in `sort_order`; each row shows its role, a
primary marker, a "Make primary" action, a role picker
(`ReferenceSelect` against `image_role`, as `ErrorsPanel` does for
`error_type`), and "Remove". A file input uploads through `api.uploadImage`
with the item id. A `ForSaleNotice` at the top with
`action="Change the photographs anyway"`, whose ticked state is carried into
every write as `acknowledgeForSale` — sticky for the panel's life, the same
decision `ErrorsPanel` makes and for the same reason: this panel writes on
each action rather than behind a Save button.

- [ ] **Step 4: Mount it in the editor**

In `ItemEditForm.jsx`, beside the `ErrorsPanel` mount:

```jsx
      <PhotosPanel itemId={itemId} saleState={item.sale_state ?? []} />
```

- [ ] **Step 5: Run the tests, gate and commit**

Run: `cd frontend && npx vitest run src/owner/pages/inventory/PhotosPanel.test.jsx src/owner/pages/inventory/ItemEditForm.test.jsx`
Expected: PASS. `ItemEditForm.test.jsx` may need its `getByRole('alert')`
queries disambiguated — the editor already has two notices and this adds a
third. Disambiguate by the checkbox label, as that file already does for the
form's own notice; **do not weaken an assertion to make it pass.**

Run: `scripts\ccweb_check.cmd fix`
Expected: exit code 0

```bash
git add frontend/src/owner/pages/inventory/PhotosPanel.jsx frontend/src/owner/pages/inventory/PhotosPanel.test.jsx frontend/src/owner/pages/inventory/ItemEditForm.jsx frontend/src/owner/pages/inventory/ItemEditForm.test.jsx
git commit -m "Let an item gain a photograph at any time"
```

---

### Task 11: `/owner/photos`, the unattached page

**Files:**
- Create: `frontend/src/owner/pages/Photos.jsx` (+ `.test.jsx`)
- Modify: `frontend/src/owner/OwnerApp.jsx`

- [ ] **Step 1: Write the failing test**

Write `frontend/src/owner/pages/Photos.test.jsx` covering:

- it lists unattached photographs, newest capture first (the API already
  orders them; the page must not re-sort into id order)
- choosing an item and confirming calls `api.attachImage` with that item id
- a linked photograph leaves the list

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx vitest run src/owner/pages/Photos.test.jsx`
Expected: FAIL, cannot resolve `./Photos`

- [ ] **Step 3: Write the page**

A grid of `thumbnail_url` thumbnails from `api.listUnattachedImages()`, each
with an item picker and a Link action calling `api.attachImage`. On success the
row leaves the list. A short docstring saying what this page is for: the
photographs the import pass could not place, and the ones detached from the
wrong item.

- [ ] **Step 4: Add the route and the nav entry**

In `OwnerApp.jsx`, beside the existing routes:

```jsx
          <Route path="/photos" element={<Photos />} />
```

Add its nav link next to the other inventory-side entries, matching the
surrounding markup exactly.

- [ ] **Step 5: Run the tests, gate and commit**

Run: `cd frontend && npx vitest run src/owner/pages/Photos.test.jsx src/owner/OwnerApp.test.jsx`
Expected: PASS

Run: `scripts\ccweb_check.cmd fix`
Expected: exit code 0

```bash
git add frontend/src/owner/pages/Photos.jsx frontend/src/owner/pages/Photos.test.jsx frontend/src/owner/OwnerApp.jsx
git commit -m "Give the unplaced photographs somewhere to be placed"
```

---

### Task 12: Mutation pass and documentation

**Files:**
- Modify: `backend/app/models/images.py` (docstring), `docs/system-administration.md`,
  `docs/specs/item-photographs-design.md` (status line)

- [ ] **Step 1: Confirm the suite is green first**

Run: `scripts\ccweb_check.cmd`
Expected: exit code 0. A mutation pass against a red suite proves nothing.

- [ ] **Step 2: Mutate each new guard call site**

One at a time. Delete the call, run the named test, confirm it FAILS, restore,
confirm it passes, confirm `git diff` on the file is empty before the next.

| # | File | Call site | Test that must go red |
|---|---|---|---|
| 1 | `routers/images.py` | `attach_image` | `test_image_links.py::test_attaching_to_a_listed_item_is_refused_until_acknowledged` |
| 2 | `routers/image_links.py` | `_guarded_link` (reached from `update_link`) | `test_image_links.py::test_detaching_from_a_listed_item_is_refused_until_acknowledged` |
| 3 | `routers/image_links.py` | `_guarded_link` (reached from `detach_link`) | same as above |

Rows 2 and 3 share one call site because both routes go through
`_guarded_link`. Delete it once and confirm the test goes red; note in your
report that one deletion covers both routes, rather than pretending two
mutations happened.

**If any mutation does not go red, stop and report it** — either the call site
is unreachable or the test does not exercise it, and both are defects.

- [ ] **Step 3: Re-confirm the primary-swap mutation**

Task 4 proved it once. Repeat it here as part of the whole-feature pass, since
intervening tasks touched `image_links.py`:

Comment out `_clear_primary(...)` in `make_primary`, run
`python -m pytest backend/tests/test_image_links.py::test_a_second_primary_replaces_the_first -v`,
confirm the `IntegrityError`, restore, confirm green.

- [ ] **Step 4: Correct `ItemImage`'s docstring**

`backend/app/models/images.py` says "camera filenames carry only a timestamp,
so linking is a manual, UI-assisted task rather than an import step." That is
no longer true: filenames now carry the item code and `app.photo_import` reads
it. Rewrite that sentence to say what is true — that filenames following the
convention are linked by the import pass, and the console places the rest —
keeping the explanation of why the column is nullable, which is still correct
and still load-bearing.

- [ ] **Step 5: Document the pass**

In `docs/system-administration.md`, beside the other passes, document
`python -m app.photo_import`: what it reads, the filename convention, that it
is a dry run without `--commit`, and what it does with a file it cannot place.
Read the surrounding entries first and match their shape.

Set the spec's status line to:

```markdown
Design. Status: **agreed with the owner 2026-09-18**; built.
```

- [ ] **Step 6: Run the gate and commit**

Run: `scripts\ccweb_check.cmd`
Expected: exit code 0

```bash
git add backend/app/models/images.py docs/system-administration.md docs/specs/item-photographs-design.md
git commit -m "Confirm the photograph guards are reached, and say so in the docs"
```

- [ ] **Step 7: Confirm the branch is ready**

Run: `git log --oneline main..HEAD`

Run `git merge-base --is-ancestor main HEAD` **as its own command**, then read
`$?` on a separate line — an `&&` chain short-circuits and reports the wrong
command's status.
Expected: 0, so a fast-forward is guaranteed.

**Do not merge and do not push.** The owner does that.
