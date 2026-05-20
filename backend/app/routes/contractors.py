"""
Contractor endpoints.

All routes require X-Organisation-Id header.
"""
from calendar import monthrange
from datetime import date
from decimal import Decimal

from flask import Blueprint, jsonify, request
from sqlalchemy import extract, func

from ..extensions import db
from ..models.contractor import Contractor
from ..models.cost_head import CostHead
from ..models.milestone import Milestone
from ..models.project import Project
from ..models.transaction import Transaction

bp = Blueprint("contractors", __name__, url_prefix="/api/v1")


def _org_id() -> str:
    org = request.headers.get("X-Organisation-Id", "").strip()
    if not org:
        raise ValueError("X-Organisation-Id header is required")
    return org


def _get_project(project_id: str, org_id: str) -> Project:
    return Project.query.filter_by(
        id=project_id, organisation_id=org_id
    ).first_or_404()


def _contractor_summary(contractor: Contractor) -> dict:
    """Contractor card: contract value, total paid, outstanding, retention held."""
    paid_row = (
        db.session.query(func.coalesce(func.sum(Transaction.amount_inr), 0))
        .filter_by(contractor_id=contractor.id)
        .scalar()
    )
    total_paid = Decimal(str(paid_row))
    contract_amt = contractor.contract_amount_inr or Decimal("0")
    retention_held = (total_paid * (contractor.retention_percentage or 0) / 100).quantize(
        Decimal("0.01")
    )
    outstanding = contract_amt - total_paid

    return {
        "id":                  str(contractor.id),
        "name":                contractor.name,
        "work_package":        contractor.work_package,
        "payment_terms":       contractor.payment_terms,
        "contract_amount_inr": float(contract_amt),
        "total_paid_inr":      float(total_paid),
        "outstanding_inr":     float(outstanding),
        "retention_held_inr":  float(retention_held),
        "retention_percentage": float(contractor.retention_percentage or 0),
    }


# ── GET /api/v1/projects/<id>/contractors ────────────────────────────────────

@bp.get("/projects/<project_id>/contractors")
def list_contractors(project_id: str):
    try:
        org_id = _org_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    _get_project(project_id, org_id)
    contractors = Contractor.query.filter_by(project_id=project_id).order_by(
        Contractor.work_package, Contractor.name
    ).all()
    return jsonify([_contractor_summary(c) for c in contractors])


# ── GET /api/v1/projects/<id>/contractors/<contractor_id>/payments ───────────

@bp.get("/projects/<project_id>/contractors/<contractor_id>/payments")
def contractor_payments(project_id: str, contractor_id: str):
    """
    Payment history for one contractor, optionally filtered by ?year= and ?month=.
    Returns transactions in chronological order with milestone linkage.
    """
    try:
        org_id = _org_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    _get_project(project_id, org_id)
    contractor = Contractor.query.filter_by(
        id=contractor_id, project_id=project_id
    ).first_or_404()

    query = Transaction.query.filter_by(
        project_id=project_id, contractor_id=contractor_id
    )

    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    if year:
        query = query.filter(extract("year", Transaction.transaction_date) == year)
    if month:
        query = query.filter(extract("month", Transaction.transaction_date) == month)

    txns = query.order_by(Transaction.transaction_date).all()

    payments = []
    for t in txns:
        milestone_name = None
        if t.milestone_id:
            m = Milestone.query.get(t.milestone_id)
            milestone_name = m.name if m else None

        payments.append({
            "id":               str(t.id),
            "payment_date":     t.transaction_date.isoformat(),
            "amount_inr":       float(t.amount_inr),
            "gst_amount_inr":   float(t.gst_amount_inr or 0),
            "tds_amount_inr":   float(t.tds_amount_inr or 0),
            "invoice_number":   t.invoice_number,
            "description":      t.description,
            "milestone_name":   milestone_name,
            "milestone_id":     str(t.milestone_id) if t.milestone_id else None,
        })

    return jsonify({
        "contractor": _contractor_summary(contractor),
        "payments":   payments,
        "total_payments": len(payments),
        "period_total_inr": sum(p["amount_inr"] for p in payments),
    })


# ── GET /api/v1/projects/<id>/contractors/monthly-summary ───────────────────

@bp.get("/projects/<project_id>/contractors/monthly-summary")
def monthly_summary(project_id: str):
    """
    Per-contractor per-month payment summary.
    Returns a grid: contractor → [{year, month, amount_paid, payment_count}]
    Optionally filter by ?year= to restrict to one calendar year.
    """
    try:
        org_id = _org_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    _get_project(project_id, org_id)

    year = request.args.get("year", type=int)

    base_q = (
        db.session.query(
            Transaction.contractor_id,
            extract("year",  Transaction.transaction_date).label("yr"),
            extract("month", Transaction.transaction_date).label("mo"),
            func.sum(Transaction.amount_inr).label("total"),
            func.count(Transaction.id).label("count"),
        )
        .filter(
            Transaction.project_id == project_id,
            Transaction.contractor_id.isnot(None),
        )
        .group_by(
            Transaction.contractor_id,
            extract("year",  Transaction.transaction_date),
            extract("month", Transaction.transaction_date),
        )
        .order_by("yr", "mo")
    )

    if year:
        base_q = base_q.filter(extract("year", Transaction.transaction_date) == year)

    rows = base_q.all()

    # Build contractor lookup
    contractor_map: dict[str, Contractor] = {
        str(c.id): c
        for c in Contractor.query.filter_by(project_id=project_id).all()
    }

    result: dict[str, dict] = {}
    for row in rows:
        cid = str(row.contractor_id)
        contractor = contractor_map.get(cid)
        if contractor is None:
            continue
        if cid not in result:
            result[cid] = {
                "contractor_id":   cid,
                "contractor_name": contractor.name,
                "work_package":    contractor.work_package,
                "monthly":         [],
            }
        result[cid]["monthly"].append({
            "year":          int(row.yr),
            "month":         int(row.mo),
            "amount_paid_inr": float(row.total),
            "payment_count": int(row.count),
        })

    return jsonify(list(result.values()))


# ── GET /api/v1/projects/<id>/contractors/milestone-payments ────────────────

@bp.get("/projects/<project_id>/contractors/milestone-payments")
def milestone_payments(project_id: str):
    """
    All payments that are tied to a specific milestone.
    Shows which milestone triggered each payment and variance (were they paid
    before or after the planned date?).
    """
    try:
        org_id = _org_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    _get_project(project_id, org_id)

    txns = (
        Transaction.query
        .filter(
            Transaction.project_id == project_id,
            Transaction.milestone_id.isnot(None),
        )
        .order_by(Transaction.transaction_date)
        .all()
    )

    contractor_map: dict[str, Contractor] = {
        str(c.id): c
        for c in Contractor.query.filter_by(project_id=project_id).all()
    }
    milestone_map: dict[str, Milestone] = {
        str(m.id): m
        for m in Milestone.query.filter_by(project_id=project_id).all()
    }

    items = []
    for t in txns:
        milestone = milestone_map.get(str(t.milestone_id))
        contractor = contractor_map.get(str(t.contractor_id)) if t.contractor_id else None

        days_after_milestone = None
        if milestone and milestone.actual_date:
            days_after_milestone = (t.transaction_date - milestone.actual_date).days

        items.append({
            "transaction_id":        str(t.id),
            "payment_date":          t.transaction_date.isoformat(),
            "amount_inr":            float(t.amount_inr),
            "invoice_number":        t.invoice_number,
            "contractor_name":       contractor.name if contractor else None,
            "work_package":          contractor.work_package if contractor else None,
            "milestone_id":          str(t.milestone_id),
            "milestone_name":        milestone.name if milestone else None,
            "milestone_phase":       milestone.phase if milestone else None,
            "milestone_planned":     milestone.planned_date.isoformat() if milestone else None,
            "milestone_actual":      milestone.actual_date.isoformat() if milestone and milestone.actual_date else None,
            "days_after_completion": days_after_milestone,
        })

    return jsonify(items)
