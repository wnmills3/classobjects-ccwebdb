"""Fetch a photograph from a web address, for filing against an item.

The console pastes an address -- an eBay listing's picture, a HiBid lot's --
and the server fetches it, so the owner need not download and re-upload it.
The bytes then go through `image_store.ingest` like any upload: converted,
stripped of metadata, stored. A marketplace serves one picture at many
sizes, and `fetch_full_size` asks for the largest whichever was pasted.

A server that fetches addresses it is handed can be pointed at the machine
it runs on or the network behind it. So only `http(s)` is fetched, and only
from a host whose every address is public: not loopback, private, link-local
(the cloud metadata address among them), multicast or reserved. Redirects are
followed by hand, each hop checked the same way, at most `_MAX_REDIRECTS`. The
body is read no further than the upload limit, and the whole fetch is given
`_MAX_SECONDS`.

The address checked is the address connected to. The host is resolved once;
the request goes to that address itself, with the host's name in the `Host`
header and as the TLS server name, so the certificate is still checked
against the name. Letting the HTTP library resolve the name again would let a
host that answers public to the check and private to the connection (DNS
rebinding) through.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from .config import settings
from .image_urls import full_size

__all__ = ["ImageFetchRefused", "fetch_full_size", "fetch_image"]

_MAX_REDIRECTS = 3
#: For each connection and each read.
_TIMEOUT = httpx.Timeout(15.0)
#: For the whole fetch, redirects included.
_MAX_SECONDS = 60.0

Resolver = Callable[..., list[Any]]


class ImageFetchRefused(ValueError):
    """The address was not fetched, or what it returned was not taken."""


def _check_host(url: str, resolve: Resolver) -> str:
    """The public address to connect to for `url`, or a refusal.

    Every address the host resolves to must be public; the first is the one
    connected to.
    """
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        # A bracket never closed, a port that is no number: what a person
        # mistyped is a refusal to show them, not a fault of the server.
        raise ImageFetchRefused(f"{url!r} cannot be read as an address: {exc}") from exc
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ImageFetchRefused(f"{url!r} is not a web address (http or https)")
    try:
        found = resolve(parts.hostname, port or 443, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError) as exc:
        raise ImageFetchRefused(f"{parts.hostname} could not be found") from exc
    addresses = [ipaddress.ip_address(sockaddr[0]) for *_rest, sockaddr in found]
    if not addresses:
        raise ImageFetchRefused(f"{parts.hostname} could not be found")
    for address in addresses:
        if not address.is_global or address.is_multicast:
            raise ImageFetchRefused(f"{parts.hostname} is not a public web address")
    return str(addresses[0])


def _pinned(url: str, address: str) -> tuple[str, str]:
    """`url` aimed at `address`, and the host name it carried."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    literal = f"[{address}]" if ":" in address else address
    netloc = f"{literal}:{parts.port}" if parts.port else literal
    return urlunsplit(parts._replace(netloc=netloc)), host


def _in_time(deadline: float) -> None:
    """Refuse a fetch that has gone on past `deadline` (a `time.monotonic` value).

    The client's timeout bounds each connection and each read, so a host
    that sends a little at a time never trips it; this bounds the whole.
    """
    if time.monotonic() > deadline:
        raise ImageFetchRefused(
            f"the address took longer than {_MAX_SECONDS:g} seconds to send the picture"
        )


def _read(response: httpx.Response, deadline: float) -> bytes:
    """A successful response's body, no larger than the upload limit."""
    if response.status_code != 200:
        raise ImageFetchRefused(f"the address answered {response.status_code}")
    body = bytearray()
    for chunk in response.iter_bytes():
        body.extend(chunk)
        if len(body) > settings.max_upload_bytes:
            raise ImageFetchRefused(
                f"the picture is larger than the {settings.max_upload_bytes} byte limit"
            )
        _in_time(deadline)
    return bytes(body)


def fetch_image(
    url: str,
    *,
    client: httpx.Client | None = None,
    resolve: Resolver = socket.getaddrinfo,
) -> bytes:
    """The bytes at `url`, following redirects that stay on public hosts.

    Raises `ImageFetchRefused` for an address that is not fetched, a response
    that is not a success, too many redirects, a body over the upload
    limit, or a fetch that takes longer than `_MAX_SECONDS` in all. `client`
    and `resolve` are for tests.
    """
    own = client is None
    session = client or httpx.Client(timeout=_TIMEOUT, follow_redirects=False)
    deadline = time.monotonic() + _MAX_SECONDS
    try:
        current = url.strip()
        for hop in range(_MAX_REDIRECTS + 1):
            if hop:
                _in_time(deadline)
            address = _check_host(current, resolve)
            target, host = _pinned(current, address)
            request = session.build_request(
                "GET",
                target,
                headers={"Host": urlsplit(current).netloc.rsplit("@", 1)[-1]},
                extensions={"sni_hostname": host},
            )
            response = session.send(request, stream=True)
            try:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ImageFetchRefused("a redirect named no address")
                    try:
                        current = urljoin(current, location)
                    except ValueError as exc:
                        raise ImageFetchRefused(
                            f"a redirect named an address that cannot be read: {exc}"
                        ) from exc
                    continue
                return _read(response, deadline)
            finally:
                response.close()
        raise ImageFetchRefused(f"more than {_MAX_REDIRECTS} redirects")
    except httpx.HTTPError as exc:
        raise ImageFetchRefused(f"the address could not be fetched: {exc}") from exc
    finally:
        if own:
            session.close()


def fetch_full_size(
    url: str, *, fetch: Callable[[str], bytes] | None = None
) -> tuple[bytes, str]:
    """The best picture there is at `url`, and the address it came from.

    The address is first rewritten to its full-size form
    (`image_urls.full_size`): a pasted thumbnail's address brings back the
    whole photograph. Where that form is refused -- the host no longer
    serves it -- the address as given is fetched instead, and its own
    refusal is the one raised. `fetch` is for tests; left out, it is
    `fetch_image`.
    """
    given = url.strip()
    largest = full_size(given)
    if largest != given:
        try:
            return (fetch or fetch_image)(largest), largest
        except ImageFetchRefused:
            pass
    return (fetch or fetch_image)(given), given
