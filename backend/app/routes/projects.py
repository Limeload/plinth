"""
Project endpoints.

All routes require X-Organisation-Id header.
"""
from decimal import Decimal

from flask import Blueprint, jsonify, request
from sqlalchemy import func

from ..extensions import db
from ..models.cost_head import CostHead
from ..models.milestone import Milestone
from ..models.project import Project
from ..models.transaction import Transaction
from ..services.ctc import compute_ctc
from ..services.health_score import compute_health_score

bp = Blueprint("projects", __name__, url_prefix="/api/v1")


def _org_id() -> str:
    org = request.headers.get("X-Organisation-Id", "").strip()
    if not org:
        raise ValueError("X-Organisation-Id header is required")
    return org


def _project_summary(p: Project) -> dict:
    return {
        "id":                       str(p.id),
        "name":                     p.name,
        "project_type":             p.project_type,
        "total_budget_inr":         float(p.total_budget_inr or 0),
        "sanctioned_loan_inr":      float(p.sanctioned_loan_inr or 0),
        "bank_name":                p.bank_name,
        "start_date":               p.start_date.isoformat() if p.start_date else None,
        "expected_completion_date": p.expected_completion_date.isoformat() if p.expected_completion_date else None,
        "rera_number":              p.rera_registration_number,
        "status":                   p.status,
    }


# ── GET /api/v1/projects ─────────────────────────────────────────────────────

@bp.get("/projects")
def list_projects():
    try:
        org_id = _org_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    projects = Project.query.filter_by(
        organisation_id=org_id, status="active"
    ).order_by(Project.created_at).all()

    return jsonify([_project_summary(p) for p in projects])


# ── GET /api/v1/projects/<id> ────────────────────────────────────────────────

@bp.get("/projects/<project_id>")
def get_project(project_id: str):
    try:
        org_id = _org_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    p = Project.query.filter_by(
        id=project_id, organisation_id=org_id
    ).first_or_404()

    # Quick stats inline — avoids a separate call for the dashboard card
    total_spent = db.session.query(
        func.coalesce(func.sum(Transaction.amount_inr), 0)
    ).filter_by(project_id=project_id).scalar()

    milestone_count = Milestone.query.filter_by(project_id=project_id).count()
    completed_count = Milestone.query.filter_by(
        project_id=project_id, actual_date=None
    ).filter(Milestone.completion_percentage >= 100).count()
    # Count milestones with actual_date set
    completed_with_date = db.session.query(func.count(Milestone.id)).filter(
        Milestone.project_id == project_id,
        Milestone.actual_date.isnot(None),
    ).scalar()
    completed_total = completed_count + (completed_with_date or 0)

    detail = _project_summary(p)
    detail["total_spent_inr"] = float(total_spent or 0)
    detail["milestones_total"] = milestone_count
    detail["milestones_completed"] = completed_total
    return jsonify(detail)


# ── GET /api/v1/projects/<id>/health ────────────────────────────────────────

@bp.get("/projects/<project_id>/cost-to-complete")
def project_ctc(project_id: str):
    """
    Returns the Cost-to-Complete forecast for a project.

    EVM per cost head:
      earned_value_inr, cpi, eac_inr, ctc_inr, budget_variance_inr, status

    Project summary:
      total_eac_inr, total_ctc_inr, total_variance_inr, project_cpi

    Time-based (burn-rate) projection:
      burn_rate_3m_inr, months_remaining, time_ctc_inr, time_efc_inr,
      months_to_finish_at_burn, schedule_risk

    Query params:
      as_of                 YYYY-MM-DD  cut-off for actuals (default today)
      override_<head_id>    float       manual completion % for that cost head
    """
    try:
        org_id = _org_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    project = Project.query.filter_by(
        id=project_id, organisation_id=org_id
    ).first_or_404()

    # Parse as_of
    as_of_raw = request.args.get("as_of")
    as_of = None
    if as_of_raw:
        from datetime import date as _date
        try:
            as_of = _date.fromisoformat(as_of_raw)
        except ValueError:
            return jsonify({"error": "as_of must be YYYY-MM-DD"}), 400

    # Collect per-head completion overrides from query string
    overrides: dict[str, float] = {}
    for k, v in request.args.items():
        if k.startswith("override_"):
            head_id = k[len("override_"):]
            try:
                overrides[head_id] = float(v)
            except ValueError:
                return jsonify({"error": f"override_{head_id} must be a number"}), 400

    result = compute_ctc(project, as_of=as_of, completion_overrides=overrides or None)
    if "error" in result:
        return jsonify(result), 422

    return jsonify(result)


@bp.get("/projects/<project_id>/health")
def project_health(project_id: str):
    """
    Returns the project health score (0–100) with component breakdown.

    score     — composite weighted score
    grade     — healthy | watch | at_risk | critical
    components
      cost      score, weight, contribution, detail
      schedule  score, weight, contribution, detail
    flags     — list of human-readable issue strings
    """
    try:
        org_id = _org_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    Project.query.filter_by(
        id=project_id, organisation_id=org_id
    ).first_or_404()

    return jsonify(compute_health_score(project_id))
