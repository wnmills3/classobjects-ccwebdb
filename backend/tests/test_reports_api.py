"""The reports API: the catalog, a result as JSON, and a result as a workbook."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from io import BytesIO

from app.models import ItemStatus
from app.reports import REPORTS
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy.orm import Session

from tests.builders import build_bare_item, build_purchase_order, code_id

_ENDPOINTS = (
    "/api/reports",
    "/api/reports/pr_outstanding",
    "/api/reports/pr_outstanding/workbook",
)


def test_the_catalog_lists_every_report_in_registry_order(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    res = client.get("/api/reports", headers=admin_headers)
    assert res.status_code == 200
    body = res.json()
    assert [entry["id"] for entry in body] == list(REPORTS)
    for entry in body:
        assert set(entry) == {"id", "group", "title", "purpose", "params"}


def test_cb_holdings_carries_its_choices_and_defaults(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    res = client.get("/api/reports", headers=admin_headers)
    entry = next(e for e in res.json() if e["id"] == "cb_holdings")
    by_name = {p["name"]: p for p in entry["params"]}
    assert by_name["status"]["type"] == "choice"
    assert by_name["status"]["default"] == "received"
    assert by_name["status"]["label"] == "Status"
    assert "ordered" in by_name["status"]["choices"]
    assert "all" in by_name["status"]["choices"]
    assert by_name["disposition"]["default"] == "held"


def test_pr_outstanding_default_and_label(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    res = client.get("/api/reports", headers=admin_headers)
    entry = next(e for e in res.json() if e["id"] == "pr_outstanding")
    assert len(entry["params"]) == 1
    param = entry["params"][0]
    assert param["name"] == "overdue_days"
    assert param["type"] == "integer"
    assert param["default"] == 21
    assert param["label"] == "Overdue after (days)"
    assert param["choices"] is None


def test_every_registered_report_runs_and_aligns_rows_and_drills(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A smoke test over the whole catalog, with just enough data to run."""
    build_bare_item(db)
    order = build_purchase_order(db, vendor_name="Outstanding Vendor", commit=False)
    item = build_bare_item(db, status_id=code_id(db, ItemStatus, "ordered"))
    item.purchase_order_id = order.id
    db.commit()

    for report_id in REPORTS:
        res = client.get(f"/api/reports/{report_id}", headers=admin_headers)
        assert res.status_code == 200, (report_id, res.text)
        body = res.json()
        assert body["id"] == report_id
        assert len(body["drills"]) == len(body["rows"])
        column_keys = {c["key"] for c in body["columns"]}
        for row in body["rows"]:
            assert set(row) == column_keys


def test_a_param_round_trips_into_the_result(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    res = client.get(
        "/api/reports/pr_outstanding?overdue_days=5", headers=admin_headers
    )
    assert res.status_code == 200
    assert res.json()["params"] == {"overdue_days": 5}


def test_an_unknown_report_is_a_404(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    assert client.get("/api/reports/nope", headers=admin_headers).status_code == 404
    res = client.get("/api/reports/nope", headers=admin_headers)
    assert res.json()["detail"] == "Report not found"
    assert (
        client.get("/api/reports/nope/workbook", headers=admin_headers).status_code
        == 404
    )


def test_a_bad_parameter_value_is_a_422(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    res = client.get(
        "/api/reports/pr_outstanding?overdue_days=0", headers=admin_headers
    )
    assert res.status_code == 422

    res = client.get("/api/reports/cb_holdings?status=bogus", headers=admin_headers)
    assert res.status_code == 422


def test_an_unknown_query_key_is_a_422(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    res = client.get("/api/reports/pr_outstanding?nope=1", headers=admin_headers)
    assert res.status_code == 422


def test_anonymous_is_refused_on_every_endpoint(client: TestClient) -> None:
    for path in _ENDPOINTS:
        assert client.get(path).status_code == 401


def test_a_customer_is_refused_on_every_endpoint(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    for path in _ENDPOINTS:
        assert client.get(path, headers=customer_headers).status_code == 403


def _header_row(sheet: Worksheet, first_label: str) -> int:
    """The row number whose first cell is `first_label` -- the header row."""
    for row in sheet.iter_rows(min_row=1, max_row=sheet.max_row, max_col=1):
        if row[0].value == first_label:
            return row[0].row
    raise AssertionError(f"no row starts with {first_label!r}")


def test_the_workbook_rows_equal_the_jsons_rows(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = build_purchase_order(
        db, vendor_name="Workbook Vendor", order_number="WB-1", commit=False
    )
    item = build_bare_item(
        db,
        status_id=code_id(db, ItemStatus, "ordered"),
        item_cost=Decimal("42.50"),
        shipping_cost=Decimal("0.00"),
        tax_rate=Decimal("0"),
    )
    item.purchase_order_id = order.id
    db.commit()

    json_res = client.get("/api/reports/pr_outstanding", headers=admin_headers)
    assert json_res.status_code == 200
    json_body = json_res.json()
    columns = json_body["columns"]

    wb_res = client.get("/api/reports/pr_outstanding/workbook", headers=admin_headers)
    assert wb_res.status_code == 200
    assert wb_res.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    disposition = wb_res.headers["content-disposition"]
    assert disposition.startswith("attachment; filename=")
    assert "pr_outstanding_" in disposition
    assert disposition.endswith('.xlsx"')

    book = load_workbook(BytesIO(wb_res.content))
    assert book.sheetnames == ["Not yet arrived"]
    sheet = book["Not yet arrived"]

    header_row = _header_row(sheet, columns[0]["label"])
    for offset, json_row in enumerate(json_body["rows"], start=1):
        row_index = header_row + offset
        for col_index, column in enumerate(columns, start=1):
            cell = sheet.cell(row=row_index, column=col_index)
            json_value = json_row[column["key"]]
            if column["kind"] == "money":
                assert cell.number_format == "#,##0.00"
                if json_value is None:
                    assert cell.value is None
                else:
                    assert Decimal(str(cell.value)) == Decimal(json_value)
            elif column["kind"] == "date":
                if json_value is None:
                    assert cell.value is None
                else:
                    assert isinstance(cell.value, (date, datetime))
                    assert cell.value.isoformat() == json_value
            else:
                # Excel cannot tell an empty string from a blank cell --
                # the same limitation `workbook_backup` works around with
                # its own sentinel for a round-tripped import; a report
                # export is read-only and a blank cell reads the same to a
                # person either way, so both collapse to `None` here.
                expected = json_value if json_value != "" else None
                assert cell.value == expected

    assert json_body["totals"] is not None
    totals_row = header_row + len(json_body["rows"]) + 1
    money_col_index = next(
        i for i, c in enumerate(columns, start=1) if c["kind"] == "money"
    )
    money_key = columns[money_col_index - 1]["key"]
    totals_cell = sheet.cell(row=totals_row, column=money_col_index)
    assert totals_cell.font.bold is True
    assert totals_cell.number_format == "#,##0.00"
    assert Decimal(str(totals_cell.value)) == Decimal(json_body["totals"][money_key])

    assert json_body["notes"]
    note_texts = {
        cell.value
        for row in sheet.iter_rows(min_row=totals_row + 1, max_col=1)
        for cell in row
        if cell.value is not None
    }
    assert set(json_body["notes"]) <= note_texts
