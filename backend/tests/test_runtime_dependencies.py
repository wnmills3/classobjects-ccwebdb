"""Every third-party module the app imports is a runtime dependency.

A package listed only under `[dependency-groups] dev` is present on every
developer machine and in every test run, so an app module importing it passes
everything here and fails only on an install without the dev group. openpyxl
sat there while reports, `app.workbook_backup` and `app.ebay_orders` imported
it. This reads the imports themselves, not a hand-kept list.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from importlib.metadata import PackageNotFoundError, packages_distributions, requires
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
APP = REPO / "backend" / "app"


def _normalise(name: str) -> str:
    """PEP 503 form, so `Pillow`, `pillow` and `python_multipart` compare."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _imported_top_level_modules() -> set[str]:
    """Top-level names of every absolute import anywhere under `app/`."""
    found: set[str] = set()
    for path in APP.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                found.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return found


def _name(spec: str) -> str:
    """The distribution name of a requirement string."""
    return _normalise(re.split(r"[\s\[<>=!~;(]", spec.strip(), maxsplit=1)[0])


def _runtime_distributions() -> set[str]:
    """`[project] dependencies` and everything they require in turn.

    Transitive, because importing `pydantic` or `starlette` directly is
    ordinary for a FastAPI app and both arrive with `fastapi`. A requirement
    that applies only under an extra is skipped: those are the ones a plain
    install can leave out.
    """
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    pending = [_name(spec) for spec in project["project"]["dependencies"]]
    seen: set[str] = set()
    while pending:
        dist = pending.pop()
        if dist in seen:
            continue
        seen.add(dist)
        try:
            reqs = requires(dist) or []
        except PackageNotFoundError:
            continue
        pending.extend(_name(r) for r in reqs if "extra ==" not in r)
    return seen


def test_every_imported_third_party_module_is_a_runtime_dependency() -> None:
    """Each import outside the stdlib and `app` resolves to a declared dist."""
    runtime = _runtime_distributions()
    installed = packages_distributions()
    missing = {}
    for module in _imported_top_level_modules() - set(sys.stdlib_module_names):
        if module in {"app", "__future__"}:
            continue
        dists = {_normalise(d) for d in installed.get(module, [])}
        assert dists, f"`{module}` is imported by app/ but no installed dist has it"
        if not dists & runtime:
            missing[module] = sorted(dists)
    assert not missing, f"imported by app/ but not in [project] dependencies: {missing}"
