"""A photograph fetched from a web address, converted, named and filed.

`app.image_fetch.fetch_image` and `POST /api/images/from-url`. No test
reaches the network: the fetch is given a mock transport and resolver, and
the endpoint's fetch is replaced.
"""

from __future__ import annotations

import io
import socket
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from app import image_fetch
from app.image_fetch import ImageFetchRefused, fetch_image
from app.models import Image, ItemImage
from fastapi.testclient import TestClient
from PIL import Image as PILImage
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.builders import ItemFactory

PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


def _webp(color: str = "navy") -> bytes:
    """A small WebP image of one colour, as a marketplace serves photographs."""
    buffer = io.BytesIO()
    PILImage.new("RGB", (64, 40), color).save(buffer, format="WEBP")
    return buffer.getvalue()


def _resolver(addresses: dict[str, str]) -> Callable[..., list[Any]]:
    """A stand-in for `getaddrinfo` that resolves each host as the table says."""

    def resolve(host: str, *_args: object, **_kwargs: object) -> list[Any]:
        """The one address the table gives the host, in `getaddrinfo`'s shape."""
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addresses[host], 443))]

    return resolve


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    """An HTTP client whose every request is answered by the handler."""
    return httpx.Client(transport=httpx.MockTransport(handler))


# -- the fetch ---------------------------------------------------------------


def test_an_image_is_fetched() -> None:
    body = _webp()
    got = fetch_image(
        "https://i.ebayimg.com/images/g/x/s-l1600.webp",
        client=_client(lambda _r: httpx.Response(200, content=body)),
        resolve=_resolver({"i.ebayimg.com": "93.184.216.34"}),
    )
    assert got == body


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/a.jpg",
        "file:///c:/secret.jpg",
        "javascript:alert(1)",
        "not a url",
    ],
)
def test_only_web_addresses_are_fetched(url: str) -> None:
    with pytest.raises(ImageFetchRefused):
        fetch_image(
            url, client=_client(lambda _r: httpx.Response(200)), resolve=_resolver({})
        )


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.5", "192.168.1.9", "169.254.169.254", "::1"]
)
def test_an_address_inside_the_network_is_refused(address: str) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Answer anything, recording that a request was made at all."""
        calls.append(request)
        return httpx.Response(200, content=b"x")

    with pytest.raises(ImageFetchRefused, match="not a public"):
        fetch_image(
            "http://inside.example/a.jpg",
            client=_client(handler),
            resolve=_resolver({"inside.example": address}),
        )
    assert calls == []


def test_a_redirect_is_checked_as_it_is_followed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        """Redirect the public host to one inside the network."""
        if request.headers["host"] == "public.example":
            return httpx.Response(
                302, headers={"location": "http://inside.example/a.jpg"}
            )
        return httpx.Response(200, content=b"never")

    with pytest.raises(ImageFetchRefused, match="not a public"):
        fetch_image(
            "https://public.example/a.jpg",
            client=_client(handler),
            resolve=_resolver(
                {"public.example": "93.184.216.34", "inside.example": "10.1.1.1"}
            ),
        )


def test_the_address_checked_is_the_address_connected_to() -> None:
    """No second lookup: a host that answers public, then private, is not followed.

    DNS rebinding: the check sees a public address, and a second resolution --
    the HTTP library's own -- could see an internal one. The request is sent to
    the checked address itself, named by its host for the server and TLS.
    """
    answers = iter(["93.184.216.34", "10.0.0.7"])
    seen: list[httpx.Request] = []

    def resolve(_host: str, *_args: object, **_kwargs: object) -> list[Any]:
        """Resolve to the next address in turn: a different one on each lookup."""
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers), 443))]

    def handler(request: httpx.Request) -> httpx.Response:
        """Answer with image bytes, recording the request as it was sent."""
        seen.append(request)
        return httpx.Response(200, content=b"img")

    fetch_image(
        "https://rebind.example/a.jpg", client=_client(handler), resolve=resolve
    )

    (request,) = seen
    assert request.url.host == "93.184.216.34"
    assert request.headers["host"] == "rebind.example"
    assert request.extensions["sni_hostname"] == "rebind.example"


def test_too_many_redirects_are_refused() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        """Redirect every request onward, without end."""
        return httpx.Response(302, headers={"location": "https://public.example/again"})

    with pytest.raises(ImageFetchRefused, match="redirect"):
        fetch_image(
            "https://public.example/a.jpg",
            client=_client(handler),
            resolve=_resolver({"public.example": "93.184.216.34"}),
        )


def test_a_failed_fetch_names_the_status() -> None:
    with pytest.raises(ImageFetchRefused, match="404"):
        fetch_image(
            "https://public.example/gone.jpg",
            client=_client(lambda _r: httpx.Response(404)),
            resolve=_resolver({"public.example": "93.184.216.34"}),
        )


def test_a_file_over_the_limit_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(image_fetch.settings, "max_upload_bytes", 10)
    with pytest.raises(ImageFetchRefused, match="larger"):
        fetch_image(
            "https://public.example/big.jpg",
            client=_client(lambda _r: httpx.Response(200, content=b"x" * 11)),
            resolve=_resolver({"public.example": "93.184.216.34"}),
        )


# -- the endpoint ------------------------------------------------------------


def _from_url(
    client: TestClient, headers: dict[str, str], **body: object
) -> httpx.Response:
    """The response to asking for a photograph to be fetched from an address."""
    return client.post("/api/images/from-url", json=body, headers=headers)


def test_a_fetched_photograph_is_converted_named_and_filed(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetched = iter([_webp("navy"), _webp("olive")])
    monkeypatch.setattr(image_fetch, "fetch_image", lambda _url: next(fetched))
    item = make_item()

    first = _from_url(
        client,
        admin_headers,
        url="https://i.ebayimg.com/a.webp",
        inventory_item_id=item.id,
        image_role="obverse",
    )
    second = _from_url(
        client,
        admin_headers,
        url="https://i.ebayimg.com/b.webp",
        inventory_item_id=item.id,
        image_role="reverse",
    )

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    rows = db.execute(
        select(
            Image.source_ref,
            Image.media_type,
            ItemImage.sort_order,
            ItemImage.is_primary,
        )
        .join(ItemImage, ItemImage.image_id == Image.id)
        .where(ItemImage.inventory_item_id == item.id)
        .order_by(ItemImage.sort_order)
    ).all()
    assert [tuple(r) for r in rows] == [
        (f"{item.item_code}_01.jpg", "image/jpeg", 1, True),
        (f"{item.item_code}_02.jpg", "image/jpeg", 2, False),
    ]
    # Each keeps the address it came from, and says so.
    assert first.json()["source_url"] == "https://i.ebayimg.com/a.webp"
    assert db.scalars(
        select(Image.source_url)
        .join(ItemImage, ItemImage.image_id == Image.id)
        .where(ItemImage.inventory_item_id == item.id)
        .order_by(ItemImage.sort_order)
    ).all() == ["https://i.ebayimg.com/a.webp", "https://i.ebayimg.com/b.webp"]


def test_a_refused_fetch_stores_nothing(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(_url: str) -> bytes:
        """Stand in for the fetch, refusing the address."""
        raise ImageFetchRefused("not a public web address")

    monkeypatch.setattr(image_fetch, "fetch_image", refuse)
    item = make_item()
    before = db.scalar(select(Image.id).order_by(Image.id.desc()).limit(1))

    res = _from_url(
        client, admin_headers, url="http://10.0.0.1/a.jpg", inventory_item_id=item.id
    )

    assert res.status_code == 422
    assert "not a public" in res.json()["detail"]
    assert db.scalar(select(Image.id).order_by(Image.id.desc()).limit(1)) == before


def test_fetching_needs_an_item_and_a_manager(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    assert (
        client.post(
            "/api/images/from-url",
            json={"url": "https://x/a.jpg", "inventory_item_id": 1},
        ).status_code
        == 401
    )
    missing = _from_url(
        client, admin_headers, url="https://x/a.jpg", inventory_item_id=999999
    )
    assert missing.status_code == 404
