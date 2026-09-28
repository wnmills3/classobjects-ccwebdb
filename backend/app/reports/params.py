"""Validate a report's parameters from a flat string mapping.

One function, called by the HTTP API (`routers/reports.py`) and the command
line (`app/reports/__main__.py`) alike, so a report is validated identically
from either: an unknown parameter name or a value pydantic rejects is caught
by the same code either way, not by two implementations that could drift
apart.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ValidationError
from pydantic_core import ErrorDetails

from .base import Report

__all__ = ["ParamError", "resolve_params"]


class ParamError(Exception):
    """A parameter name the report's model does not declare, or a bad value.

    `unknown` holds the offending names when the failure was unrecognized
    parameters; `errors` holds pydantic's own `ValidationError.errors()` when
    it was a bad value. Exactly one of the two is non-empty, so a caller (the
    router, the CLI) can tell which case it is without re-parsing the message.
    """

    def __init__(
        self,
        message: str,
        *,
        unknown: tuple[str, ...] = (),
        errors: list[ErrorDetails] | None = None,
    ) -> None:
        """Store `message` for display, plus whichever detail caused it."""
        super().__init__(message)
        self.unknown = unknown
        self.errors = errors


def resolve_params(report: Report[Any], values: Mapping[str, str]) -> BaseModel:
    """`report`'s params, validated from `values` -- a flat string mapping.

    Every key in `values` must be one the params model declares; an unknown
    key is refused (`ParamError.unknown`) rather than silently ignored and
    left to that parameter's default, since a typo'd name would otherwise run
    and never say why. A value pydantic rejects raises `ParamError.errors`,
    carrying `ValidationError.errors()` unchanged.
    """
    allowed = set(report.params.model_fields)
    unknown = tuple(sorted(key for key in values if key not in allowed))
    if unknown:
        raise ParamError(f"unknown parameter: {', '.join(unknown)}", unknown=unknown)
    try:
        return report.params.model_validate(dict(values))
    except ValidationError as exc:
        raise ParamError(str(exc), errors=exc.errors()) from exc
