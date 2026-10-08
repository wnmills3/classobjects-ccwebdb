"""Keeping text as text in a workbook cell.

A workbook cell has a type of its own, and openpyxl chooses it from what the
value looks like: a string that begins with `=` becomes a formula, and one
that spells a spreadsheet error (`#N/A`, `#DIV/0!`) becomes an error. The
text written into this system's workbooks is whatever was stored -- a
seller's listing title, a name a customer chose for an account, a note --
and none of it is the workbook writer's to have computed by whoever opens
the file. Every writer of such text passes its cells through `keep_text`.

A string beginning with `+`, `-` or `@` needs nothing: openpyxl writes it as
text already, and a spreadsheet does not compute a cell whose type is text.
Nothing is added to or taken from the value, so the cell reads back as
exactly what was stored.
"""

from __future__ import annotations

from openpyxl.cell.cell import Cell

__all__ = ["keep_text"]


def keep_text(cell: Cell) -> Cell:
    """`cell`, typed as text when what it holds is a string; any other as it is.

    Applied after the value is set, since setting a value is what chooses
    the type. A number, a date and an empty cell keep the type they have.
    """
    if isinstance(cell.value, str):
        cell.data_type = "s"
    return cell
