"""Fetch a photograph from a web address, for filing against an item.

The console pastes an address -- an eBay listing's picture, a HiBid lot's --
and the server fetches it, so the owner need not download and re-upload it.
The bytes then go through `image_store.ingest` like any upload: converted,
stripped of metadata, stored.

A server that fetches addresses it is handed can be pointed at the machine
it runs on or the network behind it. So only `http(s)` is fetched, and only
from a host whose every address is public: not loopback, private, link-local
(the cloud metadata address among them), multicast or reserved. Redirects are
followed by hand, each hop checked the same way, at most `_MAX_REDIRECTS`. The
body is read no further than the upload limit.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from .config import settings

__all__ = ["ImageFetchRefused", "fetch_image"]

_MAX_REDIRECTS = 3
_TIMEOUT = httpx.Timeout(15.0)

Resolver = Callable[..., list[Any]]


class ImageFetchRefused(ValueError):
    """The address was not fetched, or what it returned was not taken."""


def _check_host(url: str, resolve: Resolver) -> None:
    """Refuse anything but an http(s) address on a public host."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ImageFetchRefused(f"{url!r} is not a web address (http or https)")
    try:
        found = resolve(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError) as exc:
        raise ImageFetchRefused(f"{parts.hostname} could not be found") from exc
    for *_rest, sockaddr in found:
        address = ipaddress.ip_address(sockaddr[0])
        if not address.is_global or address.is_multicast:
            raise ImageFetchRefused(f"{parts.hostname} is not a public web address")


def fetch_image(
    url: str,
    *,
    client: httpx.Client | None = None,
    resolve: Resolver = socket.getaddrinfo,
) -> bytes:
    """The bytes at `url`, following redirects that stay on public hosts.

    Raises `ImageFetchRefused` for an address that is not fetched, a response
    that is not a success, too many redirects, or a body over the upload
    limit. `client` and `resolve` are for tests.
    """
    own = client is None
    session = client or httpx.Client(timeout=_TIMEOUT, follow_redirects=False)
    try:
        current = url.strip()
        for _hop in range(_MAX_REDIRECTS + 1):
            _check_host(current, resolve)
            with session.stream("GET", current) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ImageFetchRefused("a redirect named no address")
                    current = urljoin(current, location)
                    continue
                if response.status_code != 200:
                    raise ImageFetchRefused(
                        f"the address answered {response.status_code}"
                    )
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > settings.max_upload_bytes:
                        raise ImageFetchRefused(
                            "the picture is larger than the "
                            f"{settings.max_upload_bytes} byte limit"
                        )
                return bytes(body)
        raise ImageFetchRefused(f"more than {_MAX_REDIRECTS} redirects")
    except httpx.HTTPError as exc:
        raise ImageFetchRefused(f"the address could not be fetched: {exc}") from exc
    finally:
        if own:
            session.close()
