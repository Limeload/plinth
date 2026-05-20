"""
Draw report endpoints.

POST   /api/v1/projects/<id>/draw-reports            — create (calculate + persist)
GET    /api/v1/projects/<id>/draw-reports            — list
GET    /api/v1/projects/<id>/draw-reports/preview    — calculate without persisting
GET    /api/v1/projects/<id>/draw-reports/<rid>      — detail with line items
PATCH  /api/v1/projects/<id>/draw-reports/<rid>      — update status
DELETE /api/v1/projects/<id>/draw-reports/<rid>      — delete draft

All routes require X-Organisation-Id header.
"""
from datetime import date

from flask import Blueprint, jsonify, request

from ..models.draw_report import DrawReport
from ..models.project import Project
from ..services.draw_report import (
    compute_draw_request,
    create_draw_report,
    report_to_dict,
)

bp = Blueprint("draw_reports", __name__, url_prefix="/api/v1")

_VALID_STATUSES  = {"draft", "submitted", "approved", "rejected"}
_VALID_FORMATS   = {"generic", "SBI", "HDFC", "ICICI"}


def _org_id() -> str:
    org = request.headers.get("X-Organisation-Id", "").strip()
    if not org:
        raise ValueError("X-Organisation-Id header is required")
    return org


def _get_project(project_id: str, org_id: str) -> Project:
    return Project.query.filter_by(id=project_id, organisation_id=org_id).first_or_404()


def _parse_date(raw: str | None, field: str) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValueError(f"{field} must be YYYY-MM-DD")


# ── Preview — calculate without persisting ────────────────────────────────────

@bp.get("/projects/<project_id>/draw-reports/preview")
def preview_draw_report(project_id: str):
    """
    Returns the draw calculation for a project without creating any record.

    Query params:
      as_of   — cut-off date for actuals (default: today)
      override_<cost_head_id>  — manual completion % for that head (0–100)
    """
    try:
        org_id = _org_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    project = _get_project(project_id, org_id)

    try:
        as_of = _parse_date(request.args.get("as_of"), "as_of")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # collect any per-head override query params: ?override_<uuid>=<pct>
    overrides: dict[str, float] = {}
    for k, v in request.args.items():
        if k.startswith("override_"):
            head_id = k[len("override_"):]
            try:
                overrides[head_id] = float(v)
            except ValueError:
                return jsonify({"error": f"override_{head_id} must be a number"}), 400

    try:
        result = compute_draw_request(project, as_of=as_of, completion_overrides=overrides or None)
    except ValueError as e:
        return jsonify({"error": str(e)}), 422

    return jsonify(result)


# ── Create ────────────────────────────────────────────────────────────────────

@bp.post("/projects/<project_id>/draw-reports")
def create_report(project_id: str):
    """
    Body (JSON):
      report_date          string  YYYY-MM-DD  required
      period_start         string  YYYY-MM-DD  optional
      period_end           string  YYYY-MM-DD  optional
      bank_format          string  generic | SBI | HDFC | ICICI  (default generic)
      completion_overrides dict    {cost_head_id: pct}  optional
    """
    try:
        org_id = _org_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    project = _get_project(project_id, org_id)

    body = request.get_json(silent=True) or {}

    try:
        report_date  = _parse_date(body.get("report_date"), "report_date")
        period_start = _parse_date(body.get("period_start"), "period_start")
        period_end   = _parse_date(body.get("period_end"),   "period_end")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    if not report_date:
        return jsonify({"error": "report_date is required"}), 400

    bank_format = body.get("bank_format", "generic")
    if bank_format not in _VALID_FORMATS:
        return jsonify({"error": f"bank_format must be one of {sorted(_VALID_FORMATS)}"}), 400

    overrides = body.get("completion_overrides")
    if overrides is not None and not isinstance(overrides, dict):
        return jsonify({"error": "completion_overrides must be an object"}), 400

    try:
        report = create_draw_report(
            project,
            report_date=report_date,
            period_start=period_start,
            period_end=period_end,
            bank_format=bank_format,
            completion_overrides=overrides,
            generated_by="auto",
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 422

    return jsonify(report_to_dict(report)), 201


# ── List ──────────────────────────────────────────────────────────────────────

@bp.get("/projects/<project_id>/draw-reports")
def list_reports(project_id: str):
    try:
        org_id = _org_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    _get_project(project_id, org_id)  # 404 guard

    reports = (
        DrawReport.query.filter_by(project_id=project_id)
        .order_by(DrawReport.report_date.desc())
        .all()
    )
    # List view: no line items for brevity
    return jsonify([
        {
            "id":                          str(r.id),
            "report_date":                 r.report_date.isoformat(),
            "reporting_period_start":      r.reporting_period_start.isoformat() if r.reporting_period_start else None,
            "reporting_period_end":        r.reporting_period_end.isoformat() if r.reporting_period_end else None,
            "total_draw_amount_inr":       float(r.total_draw_amount_inr or 0),
            "overall_completion_percentage": float(r.overall_completion_percentage or 0),
            "bank_format":                 r.bank_format,
            "status":                      r.status,
            "generated_by":                r.generated_by,
            "created_at":                  r.created_at.isoformat(),
        }
        for r in reports
    ])


# ── Detail ────────────────────────────────────────────────────────────────────

@bp.get("/projects/<project_id>/draw-reports/<report_id>")
def get_report(project_id: str, report_id: str):
    try:
        org_id = _org_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    _get_project(project_id, org_id)

    report = DrawReport.query.filter_by(
        id=report_id, project_id=project_id
    ).first_or_404()

    return jsonify(report_to_dict(report))


# ── Status update ─────────────────────────────────────────────────────────────

@bp.patch("/projects/<project_id>/draw-reports/<report_id>")
def update_report(project_id: str, report_id: str):
    """
    Body: {"status": "submitted" | "approved" | "rejected"}
    Only status transitions are supported here; line items are immutable after creation.
    """
    try:
        org_id = _org_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    _get_project(project_id, org_id)

    report = DrawReport.query.filter_by(
        id=report_id, project_id=project_id
    ).first_or_404()

    body = request.get_json(silent=True) or {}
    new_status = body.get("status")

    if not new_status:
        return jsonify({"error": "status is required"}), 400
    if new_status not in _VALID_STATUSES:
        return jsonify({"error": f"status must be one of {sorted(_VALID_STATUSES)}"}), 400
    if report.status == "approved" and new_status != "approved":
        return jsonify({"error": "approved reports cannot be moved back"}), 409

    report.status = new_status
    from ..extensions import db
    db.session.commit()

    return jsonify({"id": str(report.id), "status": report.status})


# ── Delete draft ──────────────────────────────────────────────────────────────

@bp.delete("/projects/<project_id>/draw-reports/<report_id>")
def delete_report(project_id: str, report_id: str):
    try:
        org_id = _org_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    _get_project(project_id, org_id)

    report = DrawReport.query.filter_by(
        id=report_id, project_id=project_id
    ).first_or_404()

    if report.status != "draft":
        return jsonify({"error": "only draft reports can be deleted"}), 409

    from ..extensions import db
    db.session.delete(report)
    db.session.commit()

    return "", 204
