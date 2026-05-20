"""
Cost head endpoints.

GET /projects/<id>/cost-heads           — list with actuals
GET /projects/<id>/cost-heads/variance  — planned vs committed vs actual, with status
"""
from decimal import Decimal

from flask import Blueprint, jsonify, request
from sqlalchemy import func

from ..extensions import db
from ..models.contractor import Contractor
from ..models.cost_head import CostHead
from ..models.project import Project
from ..models.transaction import Transaction

bp = Blueprint("cost_heads", __name__, url_prefix="/api/v1")

_OVERRUN_THRESHOLD  = Decimal("1.00")   # actual / budget > 100 %
_AT_RISK_THRESHOLD  = Decimal("0.85")   # actual / budget > 85 %


def _org_id() -> str:
    org = request.headers.get("X-Organisation-Id", "").strip()
    if not org:
        raise ValueError("X-Organisation-Id header is required")
    return org


def _get_project(project_id: str, org_id: str) -> Project:
    return Project.query.filter_by(
        id=project_id, organisation_id=org_id
    ).first_or_404()


def _status(utilisation: Decimal) -> str:
    if utilisation > _OVERRUN_THRESHOLD:
        return "overrun"
    if utilisation > _AT_RISK_THRESHOLD:
        return "at_risk"
    return "on_track"


# ── GET /api/v1/projects/<id>/cost-heads ─────────────────────────────────────

@bp.get("/projects/<project_id>/cost-heads")
def list_cost_heads(project_id: str):
    try:
        org_id = _org_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    _get_project(project_id, org_id)

    # Single query: sum transactions per cost head
    actuals: dict[str, Decimal] = {
        str(row.cost_head_id): Decimal(str(row.total))
        for row in db.session.query(
            Transaction.cost_head_id,
            func.coalesce(func.sum(Transaction.amount_inr), 0).label("total"),
        )
        .filter(Transaction.project_id == project_id)
        .group_by(Transaction.cost_head_id)
        .all()
    }

    heads = CostHead.query.filter_by(project_id=project_id).order_by(
        CostHead.category
    ).all()

    return jsonify([
        {
            "id":               str(h.id),
            "name":             h.name,
            "category":         h.category,
            "budgeted_inr":     float(h.budgeted_amount_inr),
            "actual_inr":       float(actuals.get(str(h.id), Decimal("0"))),
        }
        for h in heads
    ])


# ── GET /api/v1/projects/<id>/cost-heads/variance ────────────────────────────

@bp.get("/projects/<project_id>/cost-heads/variance")
def cost_variance(project_id: str):
    """
    Returns per-cost-head variance and a project-level rollup.

    Per cost head:
      budgeted_inr        — from cost_heads.budgeted_amount_inr
      committed_inr       — sum of contractor contract amounts for that category
      actual_inr          — sum of transactions.amount_inr
      variance_inr        — budgeted - actual  (negative = overrun)
      variance_pct        — variance / budgeted × 100  (null if budgeted = 0)
      utilisation_pct     — actual / budgeted × 100  (null if budgeted = 0)
      status              — on_track | at_risk | overrun | no_budget

    Project rollup (same fields, summed across all heads).
    """
    try:
        org_id = _org_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    _get_project(project_id, org_id)

    # Actuals: sum transactions per cost head
    actuals: dict[str, Decimal] = {
        str(row.cost_head_id): Decimal(str(row.total))
        for row in db.session.query(
            Transaction.cost_head_id,
            func.coalesce(func.sum(Transaction.amount_inr), 0).label("total"),
        )
        .filter(Transaction.project_id == project_id)
        .group_by(Transaction.cost_head_id)
        .all()
    }

    # Committed: sum contractor contract amounts per category
    committed_by_category: dict[str, Decimal] = {}
    for c in Contractor.query.filter_by(project_id=project_id).all():
        cat = c.work_package or "Misc"
        committed_by_category[cat] = (
            committed_by_category.get(cat, Decimal("0"))
            + (c.contract_amount_inr or Decimal("0"))
        )

    heads = CostHead.query.filter_by(project_id=project_id).order_by(
        CostHead.category
    ).all()

    line_items = []
    total_budgeted  = Decimal("0")
    total_committed = Decimal("0")
    total_actual    = Decimal("0")

    for h in heads:
        budgeted  = h.budgeted_amount_inr or Decimal("0")
        committed = committed_by_category.get(h.category, Decimal("0"))
        actual    = actuals.get(str(h.id), Decimal("0"))

        variance_inr = budgeted - actual
        if budgeted > 0:
            variance_pct     = float((variance_inr / budgeted * 100).quantize(Decimal("0.1")))
            utilisation_pct  = float((actual / budgeted * 100).quantize(Decimal("0.1")))
            status           = _status(actual / budgeted)
        else:
            variance_pct    = None
            utilisation_pct = None
            status          = "no_budget"

        # Exposure: committed but not yet paid
        unpaid_committed = max(committed - actual, Decimal("0"))

        line_items.append({
            "id":               str(h.id),
            "name":             h.name,
            "category":         h.category,
            "budgeted_inr":     float(budgeted),
            "committed_inr":    float(committed),
            "actual_inr":       float(actual),
            "unpaid_committed_inr": float(unpaid_committed),
            "variance_inr":     float(variance_inr),
            "variance_pct":     variance_pct,
            "utilisation_pct":  utilisation_pct,
            "status":           status,
        })

        total_budgeted  += budgeted
        total_committed += committed
        total_actual    += actual

    total_variance = total_budgeted - total_actual
    project_utilisation = (
        float((total_actual / total_budgeted * 100).quantize(Decimal("0.1")))
        if total_budgeted > 0 else None
    )
    project_variance_pct = (
        float((total_variance / total_budgeted * 100).quantize(Decimal("0.1")))
        if total_budgeted > 0 else None
    )

    return jsonify({
        "project_id": project_id,
        "summary": {
            "budgeted_inr":        float(total_budgeted),
            "committed_inr":       float(total_committed),
            "actual_inr":          float(total_actual),
            "variance_inr":        float(total_variance),
            "variance_pct":        project_variance_pct,
            "utilisation_pct":     project_utilisation,
            "overrun_heads":       [i["category"] for i in line_items if i["status"] == "overrun"],
            "at_risk_heads":       [i["category"] for i in line_items if i["status"] == "at_risk"],
        },
        "line_items": line_items,
    })
