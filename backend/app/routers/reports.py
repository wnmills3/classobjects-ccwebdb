"""The reports API: the catalog, a result as JSON, and a result as a workbook.

Manager only, like everything else that shows cost, value or location --
nothing here reaches the shop. A report's own parameters model does the
validating: FastAPI cannot bind a query string at this endpoint's own
signature, since every report's parameters differ, so this router reads the
raw query string and hands it to `reports.params.resolve_params` -- shared
with the command line, so a report is validated identically from either --
which refuses any key the report's model does not declare (an allowlist,
never a name trusted straight into a query) and otherwise runs that model's
own `model_validate`, so a bad value comes back 422 with pydantic's own error
detail.
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from starlette.datastructures import QueryParams

from ..deps import AdminUser, DbSession
from ..reports.base import Report
from ..reports.params import ParamError, resolve_params
from ..reports.registry import REPORTS
from ..reports.serialize import catalog, serialize_result
from ..reports.workbook import workbook_filename, write_workbook

router = APIRouter(prefix="/reports", tags=["reports"])

_REPORT_NOT_FOUND = "Report not found"
_WORKBOOK_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


def _report_or_404(report_id: str) -> Report[Any]:
    """The registered report named `report_id`, or a 404."""
    report = REPORTS.get(report_id)
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_REPORT_NOT_FOUND
        )
    return report


def _params_from_query(report: Report[Any], query: QueryParams) -> BaseModel:
    """`report`'s params, validated from the raw query string, or a 422.

    An unknown key gets the same 422 shape a bad value gets, rather than
    being silently ignored, since a typo'd parameter name would otherwise
    run with that parameter's default and never tell the caller why.
    """
    try:
        return resolve_params(report, query)
    except ParamError as exc:
        if exc.unknown:
            detail = [
                {
                    "type": "extra_forbidden",
                    "loc": ["query", key],
                    "msg": "Unknown parameter",
                }
                for key in exc.unknown
            ]
        else:
            detail = jsonable_encoder(exc.errors)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail
        ) from exc


@router.get("")
def list_reports(_admin: AdminUser) -> list[dict[str, object]]:
    """The catalog: every registered report, its group, purpose and parameters."""
    return catalog()


@router.get("/{report_id}")
def get_report(
    report_id: str, request: Request, db: DbSession, _admin: AdminUser
) -> dict[str, object]:
    """One report's result, run with the query string's parameters, as JSON."""
    report = _report_or_404(report_id)
    params = _params_from_query(report, request.query_params)
    result = report.run(db, params)
    run_at = datetime.now().astimezone()
    return serialize_result(report, params, result, run_at)


@router.get("/{report_id}/workbook")
def get_report_workbook(
    report_id: str, request: Request, db: DbSession, _admin: AdminUser
) -> Response:
    """The same result as an `.xlsx` download, one sheet, ready to save."""
    report = _report_or_404(report_id)
    params = _params_from_query(report, request.query_params)
    result = report.run(db, params)
    run_at = datetime.now().astimezone()
    book = write_workbook(report, params, result, run_at)
    buffer = BytesIO()
    book.save(buffer)
    filename = workbook_filename(report.id, run_at)
    return Response(
        content=buffer.getvalue(),
        media_type=_WORKBOOK_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
