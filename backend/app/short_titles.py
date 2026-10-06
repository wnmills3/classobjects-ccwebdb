"""What a title that is only a face value looks like.

Its own module because two that cannot import each other both read it:
`app.seller_titles` moves the seller's text into such a title, and
`app.series_classify` tells a lot's listing from one by it.
"""

from __future__ import annotations

__all__ = ["SHORT_TITLE"]

#: A title this short is a face value or a denomination -- "1", "0.005",
#: "$1 Bill", "$1000 Bill" -- not a name for the piece.
SHORT_TITLE = 10
