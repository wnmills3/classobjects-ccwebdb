"""`app.image_urls.full_size`: a picture's address at the largest size served."""

from __future__ import annotations

import pytest
from app.image_urls import full_size, named_edge

EBAY = "https://i.ebayimg.com/images/g/90cAAeSw0l5qrTHf"
NGC = "https://ccg-imaging-ngc-coins-production.s3.amazonaws.com/17803211-25b7"


@pytest.mark.parametrize(
    ("given", "largest"),
    [
        # eBay: whatever size was copied, the same picture at 1600.
        (f"{EBAY}/s-l140.webp", f"{EBAY}/s-l1600.webp"),
        (f"{EBAY}/s-l500.jpg", f"{EBAY}/s-l1600.jpg"),
        (f"{EBAY}/s-l960.png", f"{EBAY}/s-l1600.png"),
        # Asking for more than eBay keeps returns the 1600 file: named as it is.
        (f"{EBAY}/s-l2400.jpg", f"{EBAY}/s-l1600.jpg"),
        (f"{EBAY}/S-L225.JPG", f"{EBAY}/s-l1600.JPG"),
        # The query and fragment are the address's own and stay.
        (
            f"{EBAY}/s-l300.jpg?set_id=8800005007#x",
            f"{EBAY}/s-l1600.jpg?set_id=8800005007#x",
        ),
        (
            "http://thumbs.ebayimg.com/g/abc/s-l64.jpg",
            "http://thumbs.ebayimg.com/g/abc/s-l1600.jpg",
        ),
        ("  " + f"{EBAY}/s-l140.webp" + "\n", f"{EBAY}/s-l1600.webp"),
        # NGC: a thumbnail is the full picture's name, marked.
        (f"{NGC}/TN_NGC8849054-176_OBV.jpg", f"{NGC}/NGC8849054-176_OBV.jpg"),
        (f"{NGC}/NGC3975857-136_REV@200X300.JPG", f"{NGC}/NGC3975857-136_REV.JPG"),
        (f"{NGC}/TN_NGC1-1_OBV@200x300.jpg", f"{NGC}/NGC1-1_OBV.jpg"),
    ],
)
def test_a_scaled_address_becomes_its_full_size_form(given: str, largest: str) -> None:
    assert full_size(given) == largest


@pytest.mark.parametrize(
    "address",
    [
        # Already the full size.
        f"{EBAY}/s-l1600.webp",
        f"{NGC}/NGC8849054-176_OBV.jpg",
        # eBay, but not a picture file of the sized kind.
        "https://i.ebayimg.com/images/g/abc/original.jpg",
        "https://i.ebayimg.com/images/g/s-l500.jpg/extra",
        "https://www.ebay.com/itm/137199009121",
        # Another host's file that only looks like eBay's or NGC's.
        "https://example.com/images/s-l500.jpg",
        "https://notebayimg.com/g/abc/s-l500.jpg",
        "https://images.example.com/TN_coin@200X300.jpg",
        # Not a web address: returned as given, for the caller to refuse.
        "ftp://i.ebayimg.com/images/g/abc/s-l500.jpg",
        "s-l500.jpg",
        "",
    ],
)
def test_any_other_address_is_returned_as_given(address: str) -> None:
    assert full_size(address) == address


@pytest.mark.parametrize(
    ("address", "edge"),
    [
        (f"{EBAY}/s-l140.webp", 140),
        (f"{EBAY}/s-l500.jpg", 500),
        (f"{EBAY}/s-l1600.jpg", 1600),
        ("https://I.EBAYIMG.COM/g/abc/S-L64.JPG", 64),
        # No size in the name, or not eBay's to read.
        (f"{EBAY}/original.jpg", None),
        (f"{NGC}/TN_NGC8849054-176_OBV.jpg", None),
        ("https://example.com/images/s-l500.jpg", None),
        ("https://notebayimg.com/g/abc/s-l500.jpg", None),
        ("", None),
    ],
)
def test_an_ebay_address_names_the_edge_its_picture_fits(
    address: str, edge: int | None
) -> None:
    assert named_edge(address) == edge


def test_the_host_is_matched_whatever_its_capitals() -> None:
    assert (
        full_size("https://I.EBAYIMG.COM/images/g/abc/s-l500.jpg")
        == "https://I.EBAYIMG.COM/images/g/abc/s-l1600.jpg"
    )
