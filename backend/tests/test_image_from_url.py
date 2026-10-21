"""A photograph fetched from a web address, converted, named and filed.

`app.image_fetch.fetch_image` and `POST /api/images/from-url`. No test
reaches the network: the fetch is given a mock transport and resolver, and
the endpoint's fetch is replaced.
"""

from __future__ import annotations

import io
import socket
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from app import image_fetch
from app.config import settings
from app.image_fetch import ImageFetchRefused, fetch_image
from app.models import Image, InventoryItem, ItemImage, Listing
from fastapi.testclient import TestClient
from PIL import Image as PILImage
from sqlalchemy import select, update
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
    "address",
    ["127.0.0.1", "10.0.0.5", "192.168.1.9", "169.254.169.254", "::1", "224.0.0.1"],
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


def test_a_host_with_any_address_inside_the_network_is_refused() -> None:
    """Every address the host gives is checked, not only the first."""
    calls: list[httpx.Request] = []

    def resolve(_host: str, *_args: object, **_kwargs: object) -> list[Any]:
        """A public address first, then one inside the network."""
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443)),
        ]

    def handler(request: httpx.Request) -> httpx.Response:
        """Answer anything, recording that a request was made at all."""
        calls.append(request)
        return httpx.Response(200, content=b"x")

    with pytest.raises(ImageFetchRefused, match="not a public"):
        fetch_image(
            "http://mixed.example/a.jpg", client=_client(handler), resolve=resolve
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


@pytest.mark.parametrize(
    "url",
    [
        "http://public.example:abc/a.jpg",
        "http://public.example:99999/a.jpg",
        "http://[::1/a.jpg",
        # A control character no HTTP request may carry.
        f"http://public.example/a{chr(1)}.jpg",
    ],
)
def test_an_address_that_cannot_be_read_is_refused_not_an_error(url: str) -> None:
    """A port that is no number, a bracket never closed, or a control character.

    The caller turns `ImageFetchRefused` into a message for the person who
    typed the address; any other exception reaches them as a server error.
    """
    with pytest.raises(ImageFetchRefused):
        image_fetch.fetch_full_size(
            url,
            fetch=lambda address: fetch_image(
                address,
                client=_client(lambda _r: httpx.Response(200, content=b"x")),
                resolve=_resolver({"public.example": "93.184.216.34"}),
            ),
        )


def test_a_fetch_that_outlasts_its_time_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole fetch has a limit, not only each read of it.

    A host that sends a little at a time never trips the per-read timeout.
    The limit is put in the past here, so the first chunk read is already
    late.
    """
    monkeypatch.setattr(image_fetch, "_MAX_SECONDS", -1.0, raising=False)
    with pytest.raises(ImageFetchRefused, match="took longer"):
        fetch_image(
            "https://public.example/slow.jpg",
            client=_client(lambda _r: httpx.Response(200, content=b"x" * 64)),
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


def test_fetching_onto_an_item_for_sale_is_refused_until_acknowledged(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    listing: Listing,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shop shows the item's photographs, so adding one is acknowledged."""
    asked: list[str] = []

    def fetch(url: str) -> bytes:
        """Answer with a picture, noting that the address was fetched."""
        asked.append(url)
        return _webp("teal")

    monkeypatch.setattr(image_fetch, "fetch_image", fetch)
    body = {
        "url": "https://public.example/a.webp",
        "inventory_item_id": listing.inventory_item_id,
    }

    refused = _from_url(client, admin_headers, **body)
    assert refused.status_code == 409, refused.text
    assert "For sale" in refused.json()["detail"]
    # Refused before anything is fetched or filed.
    assert asked == []
    assert (
        db.scalar(
            select(ItemImage.id).where(
                ItemImage.inventory_item_id == listing.inventory_item_id
            )
        )
        is None
    )

    made = _from_url(client, admin_headers, **body, acknowledge_for_sale=True)
    assert made.status_code == 201, made.text
    assert asked == ["https://public.example/a.webp"]


def test_a_fetch_naming_an_unknown_role_stores_nothing(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The role is refused before the bytes are written, so no file is orphaned."""
    monkeypatch.setattr(image_fetch, "fetch_image", lambda _url: _webp("maroon"))
    item = make_item()

    res = _from_url(
        client,
        admin_headers,
        url="https://public.example/a.webp",
        inventory_item_id=item.id,
        image_role="no-such-role",
    )

    assert res.status_code == 422, res.text
    assert "image_role" in res.json()["detail"]
    root = Path(settings.media_root)
    assert [p for p in root.rglob("*") if p.is_file()] == []


def test_an_address_too_long_to_keep_at_full_size_stores_nothing(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The full-size form of an address is longer than the one pasted.

    The address kept is the one the bytes came from, and a column holds it:
    one that will not fit is refused with a reason, not a server error.
    """
    monkeypatch.setattr(image_fetch, "fetch_image", lambda _url: _webp("plum"))
    item = make_item()
    head, tail = "https://i.ebayimg.com/images/g/", "/s-l140.webp"
    url = head + "x" * (2000 - len(head) - len(tail)) + tail
    assert len(url) == 2000

    res = _from_url(client, admin_headers, url=url, inventory_item_id=item.id)

    assert res.status_code == 422, res.text
    assert "characters" in res.json()["detail"]
    db.expire_all()
    filed = select(ItemImage.id).where(ItemImage.inventory_item_id == item.id)
    assert db.scalar(filed) is None


def test_fetching_onto_a_deleted_item_fetches_nothing(
    client: TestClient,
    admin_headers: dict[str, str],
    make_item: ItemFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deleted item should never have existed: nothing is filed on it."""
    asked: list[str] = []

    def fetch(url: str) -> bytes:
        """Answer with a picture, noting that the address was fetched."""
        asked.append(url)
        return _webp("gray")

    monkeypatch.setattr(image_fetch, "fetch_image", fetch)
    item = make_item(deleted_at=datetime(2026, 1, 1, tzinfo=UTC))

    res = _from_url(
        client,
        admin_headers,
        url="https://public.example/a.webp",
        inventory_item_id=item.id,
    )

    assert res.status_code == 422, res.text
    assert "deleted" in res.json()["detail"]
    assert asked == []


def test_no_transaction_is_held_open_while_fetching(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fetch can take a minute; the rows read before it are not held over it."""
    during: list[bool] = []

    def fetch(_url: str) -> bytes:
        """Answer with a picture, noting whether a transaction is open."""
        during.append(db.in_transaction())
        return _webp("khaki")

    monkeypatch.setattr(image_fetch, "fetch_image", fetch)
    item = make_item()

    res = _from_url(
        client,
        admin_headers,
        url="https://public.example/a.webp",
        inventory_item_id=item.id,
    )

    assert res.status_code == 201, res.text
    assert during == [False]


def test_an_item_deleted_while_fetching_is_not_filed_on(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The item is checked again once the bytes are in, not only before."""
    item = make_item()
    item_id = item.id

    def fetch(_url: str) -> bytes:
        """Answer with a picture, the item deleted by someone else meanwhile."""
        db.execute(
            update(InventoryItem)
            .where(InventoryItem.id == item_id)
            .values(deleted_at=datetime(2026, 1, 1, tzinfo=UTC))
        )
        db.commit()
        return _webp("coral")

    monkeypatch.setattr(image_fetch, "fetch_image", fetch)

    res = _from_url(
        client,
        admin_headers,
        url="https://public.example/a.webp",
        inventory_item_id=item_id,
    )

    assert res.status_code == 422, res.text
    assert "deleted" in res.json()["detail"]
    db.expire_all()
    filed = select(ItemImage.id).where(ItemImage.inventory_item_id == item_id)
    assert db.scalar(filed) is None
    root = Path(settings.media_root)
    assert [p for p in root.rglob("*") if p.is_file()] == []


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
    assert missing.status_code == 422
