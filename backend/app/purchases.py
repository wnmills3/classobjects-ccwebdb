"""Text conventions a purchase order's own fields follow.

Neutral ground for the two regexes `routers.acquisitions` (receiving) and
`reports.data_quality` (`dq_purchases`) both need to read the same way: a
report that restated either pattern instead of importing it could drift from
what receiving itself accepts, and a report has no business importing a
router just to reach one regex.
"""

from __future__ import annotations

import re

__all__ = ["GENERATED", "WEB_ADDRESS"]

#: `purchase_order.source_url` is free text, not necessarily a URL -- an
#: eBay listing page, an eBay order page, or the literal word
#: "Gift". Only a value that looks like a web address is ever offered as a
#: link; anything else, `javascript:` included, is withheld.
WEB_ADDRESS = re.compile(r"^https?://", re.IGNORECASE)

#: A generated order number: `Order-0001`, `Order-0002`, ... A purchase with
#: no number of its own could not be found by one; this gives it one, above
#: the highest already issued.
GENERATED = re.compile(r"^Order-(\d+)$")
