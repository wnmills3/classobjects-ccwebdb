"""Agreements between files that no single module's tests can see.

Each is a place two files must say the same thing -- a key the code reads
and the example that documents it, a script and its twin in the other
shell -- where nothing else would notice them drifting apart.
"""

from __future__ import annotations

import re

from app.config import Settings
from app.runtime_settings import REPO_ROOT

#: A key in .env.example, written out or commented out with its default.
_EXAMPLE_KEY = re.compile(r"^#? ?([A-Z][A-Z0-9_]+)=", re.MULTILINE)


def test_env_example_names_every_setting_and_no_other() -> None:
    """Every key the code reads is documented; nothing documented is unread."""
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    documented = set(_EXAMPLE_KEY.findall(text))
    read = {name.upper() for name in Settings.model_fields}
    assert sorted(read - documented) == [], "read by the code, not in .env.example"
    assert sorted(documented - read) == [], "in .env.example, read by nothing"
