"""An edit names the version it was made against, or it is refused.

Made against nothing, a save would overwrite whatever another person had
saved since, unseen. The item editor may send `base` instead
(`test_field_merge.py`); these five take a version only, an
auction lot's being its auction's.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize(
    ("url", "body"),
    [
        ("/api/listings/1", {"title": "Renamed"}),
        ("/api/sales-lots/1", {"title": "Renamed"}),
        ("/api/sales-venues/store", {"name": "Renamed"}),
        ("/api/auctions/1", {"notes": "Renamed"}),
        ("/api/auctions/1/lots/1", {"lot_number": "1A"}),
    ],
    ids=["listing", "lot", "platform", "auction", "auction lot"],
)
def test_an_edit_without_a_version_is_refused(
    client: TestClient, admin_headers: dict[str, str], url: str, body: dict[str, str]
) -> None:
    refused = client.patch(url, json=body, headers=admin_headers)

    assert refused.status_code == 422, refused.text
    missing = [error["loc"] for error in refused.json()["detail"]]
    assert ["body", "version"] in missing
