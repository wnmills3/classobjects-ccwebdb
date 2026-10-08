"""The full-size form of a picture's web address.

A marketplace serves one photograph at many sizes, and the size is part of
the address: eBay's `.../s-l140.webp` is the same picture as
`.../s-l1600.webp`, forty times smaller. The address a person copies is
whichever size the page happened to show -- usually a thumbnail. What is
stored should be the best picture there is, so the address is rewritten to
its full-size form before it is fetched.

Only hosts whose scheme is known are rewritten, each by its own rule;
any other address comes back as it was given. A rewritten address may not
exist (a host can change its scheme), so a caller that fetches one falls
back to the address as given -- `image_fetch.fetch_full_size` does.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

__all__ = ["EBAY_FULL_SIZE", "full_size", "named_edge"]

#: The largest size eBay keeps: asking for more returns this same file.
EBAY_FULL_SIZE = 1600

#: eBay's picture file: `s-l` and the longest edge in pixels, then the format.
_EBAY_FILE = re.compile(r"^s-l(\d+)\.(\w+)$", re.IGNORECASE)

#: NGC's certificate pictures: a thumbnail is the full picture's name with
#: `TN_` in front, or with `@<width>X<height>` before the extension.
_NGC_HOST = "ccg-imaging-ngc-coins-production.s3.amazonaws.com"
_NGC_THUMBNAIL = re.compile(r"^TN_", re.IGNORECASE)
_NGC_SCALED = re.compile(r"@\d+X\d+(?=\.\w+$)", re.IGNORECASE)


def _ebay(name: str) -> str:
    """An eBay picture file's name at full size; any other name unchanged."""
    match = _EBAY_FILE.match(name)
    if match is None:
        return name
    return f"s-l{EBAY_FULL_SIZE}.{match.group(2)}"


def _ngc(name: str) -> str:
    """An NGC picture file's name without its thumbnail marks."""
    return _NGC_SCALED.sub("", _NGC_THUMBNAIL.sub("", name))


def named_edge(url: str) -> int | None:
    """The longest edge, in pixels, an eBay address asks its picture to fit.

    None for any other address: an NGC thumbnail's name does not say its
    size, and another host's says nothing this module reads.

    A picture held larger than its address names was not fetched at that
    size -- the address on record is a smaller form of the one it came from
    -- so it has nothing to gain from being fetched again.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if not (host == "ebayimg.com" or host.endswith(".ebayimg.com")):
        return None
    match = _EBAY_FILE.match(parts.path.rpartition("/")[2])
    return int(match.group(1)) if match else None


def full_size(url: str) -> str:
    """`url` rewritten to the largest picture its host serves.

    Returned unchanged when the host is not one whose sizes are known, when
    the address is already the full size, or when it is not a web address
    at all -- one that cannot even be read as an address among them, which
    is the fetch's to refuse.
    """
    address = url.strip()
    try:
        parts = urlsplit(address)
    except ValueError:
        return url
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        return url
    directory, slash, name = parts.path.rpartition("/")
    if host == "ebayimg.com" or host.endswith(".ebayimg.com"):
        renamed = _ebay(name)
    elif host == _NGC_HOST:
        renamed = _ngc(name)
    else:
        return url
    if renamed == name:
        return url
    return urlunsplit(parts._replace(path=f"{directory}{slash}{renamed}"))
