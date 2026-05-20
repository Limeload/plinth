"""
Draw report calculation engine.

For each cost head:
  loan_allocation  = sanctioned_loan × (head_budget / total_project_budget)
  draw_amount_due  = loan_allocation × (completion_pct / 100)
  previously_drawn = apportioned from total disbursements by loan-allocation share
  balance_to_draw  = max(0, draw_amount_due − previously_drawn)

completion_pct defaults to spend-based (actual_spend / budget × 100) but can be
overridden per cost head by the caller (manual entry in the UI).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import func

from ..extensions import db
from ..models.cost_head import CostHead
from ..models.disbursement import Disbursement
from ..models.draw_report import DrawReport, DrawReportLineItem
from ..models.transaction import Transaction


# ── Core calculation ──────────────────────────────────────────────────────────

def _actuals_by_head(project_id, as_of: date | None) -> dict[str, float]:
    q = (
        db.session.query(
            Transaction.cost_head_id,
            func.sum(Transaction.amount_inr).label("total"),
        )
        .filter(Transaction.project_id == project_id)
    )
    if as_of:
        q = q.filter(Transaction.transaction_date <= as_of)
    return {str(r.cost_head_id): float(r.total) for r in q.group_by(Transaction.cost_head_id).all()}


def _total_disbursed(project_id, as_of: date | None) -> float:
    q = Disbursement.query.filter_by(project_id=project_id)
    if as_of:
        q = q.filter(Disbursement.disbursement_date <= as_of)
    return float(sum(d.amount_inr for d in q.all()) or 0)


def compute_draw_request(
    project,
    as_of: date | None = None,
    completion_overrides: dict[str, float] | None = None,
) -> dict:
    """
    Compute a draw request without persisting anything.

    project              — Project ORM instance
    as_of                — cut-off date for actuals and disbursements; defaults to today
    completion_overrides — {cost_head_id: pct (0–100)} to override spend-based completion

    Returns a dict with summary totals and per-cost-head line_items.
    Raises ValueError if the project is missing required fields.
    """
    as_of = as_of or date.today()
    overrides = completion_overrides or {}

    sanctioned = float(project.sanctioned_loan_inr or 0)
    if sanctioned <= 0:
        raise ValueError("Project has no sanctioned loan amount")

    heads = (
        CostHead.query.filter_by(project_id=project.id)
        .order_by(CostHead.category)
        .all()
    )
    if not heads:
        raise ValueError("Project has no cost heads")

    total_budget = sum(float(h.budgeted_amount_inr or 0) for h in heads)
    if total_budget <= 0:
        raise ValueError("Project cost heads have zero total budget")

    actuals      = _actuals_by_head(project.id, as_of)
    total_disb   = _total_disbursed(project.id, as_of)

    # ── Per cost-head line items ──────────────────────────────────────────────

    items: list[dict] = []
    total_draw_due = 0.0

    for h in heads:
        budget = float(h.budgeted_amount_inr or 0)
        if budget <= 0:
            continue

        loan_alloc    = sanctioned * (budget / total_budget)
        actual_spend  = actuals.get(str(h.id), 0.0)

        if str(h.id) in overrides:
            completion_pct    = max(0.0, min(100.0, float(overrides[str(h.id)])))
            completion_source = "manual"
        else:
            completion_pct    = min(actual_spend / budget * 100.0, 100.0)
            completion_source = "spend"

        draw_due = loan_alloc * (completion_pct / 100.0)

        items.append({
            "cost_head_id":         str(h.id),
            "category":             h.category,
            "name":                 h.name,
            "budgeted_amount_inr":  round(budget),
            "loan_allocation_inr":  round(loan_alloc),
            "spent_to_date_inr":    round(actual_spend),
            "completion_percentage": round(completion_pct, 2),
            "completion_source":    completion_source,
            "draw_amount_due_inr":  round(draw_due),
            # filled in below once we know total_draw_due
            "previously_drawn_inr": 0,
            "balance_to_draw_inr":  0,
        })
        total_draw_due += draw_due

    # ── Apportion previously-drawn by loan-allocation share ───────────────────
    # Uses draw_amount_due share; falls back to loan_allocation share if no draw due yet.

    total_loan_alloc = sum(it["loan_allocation_inr"] for it in items)

    for it in items:
        if total_draw_due > 0:
            share = it["draw_amount_due_inr"] / total_draw_due
        elif total_loan_alloc > 0:
            share = it["loan_allocation_inr"] / total_loan_alloc
        else:
            share = 0.0

        prev_drawn = total_disb * share
        it["previously_drawn_inr"] = round(prev_drawn)
        it["balance_to_draw_inr"]  = round(max(0.0, it["draw_amount_due_inr"] - prev_drawn))

    # ── Summary ───────────────────────────────────────────────────────────────

    total_balance = sum(it["balance_to_draw_inr"] for it in items)
    total_budget_all = sum(it["budgeted_amount_inr"] for it in items)
    overall_completion = (
        sum(it["completion_percentage"] * it["budgeted_amount_inr"] for it in items)
        / total_budget_all
        if total_budget_all > 0 else 0.0
    )
    undrawn_sanctioned = max(0.0, sanctioned - total_disb)

    return {
        "project_id":                 str(project.id),
        "project_name":               project.name,
        "bank_name":                  project.bank_name,
        "as_of_date":                 as_of.isoformat(),
        "sanctioned_loan_inr":        round(sanctioned),
        "total_budget_inr":           round(total_budget),
        "loan_to_budget_ratio":       round(sanctioned / total_budget, 4),
        "total_draw_amount_due_inr":  round(total_draw_due),
        "total_previously_drawn_inr": round(total_disb),
        "total_balance_to_draw_inr":  total_balance,
        "undrawn_sanctioned_inr":     round(undrawn_sanctioned),
        "overall_completion_pct":     round(overall_completion, 2),
        "line_items":                 items,
    }


# ── Persistence ───────────────────────────────────────────────────────────────

def create_draw_report(
    project,
    report_date: date,
    period_start: date | None = None,
    period_end: date | None = None,
    bank_format: str = "generic",
    completion_overrides: dict[str, float] | None = None,
    generated_by: str = "auto",
) -> DrawReport:
    """
    Runs compute_draw_request, persists a DrawReport + DrawReportLineItems,
    and returns the committed ORM object.

    Raises ValueError (from compute_draw_request) if the project is misconfigured.
    """
    calc = compute_draw_request(
        project,
        as_of=report_date,
        completion_overrides=completion_overrides,
    )

    report = DrawReport(
        project_id=project.id,
        report_date=report_date,
        reporting_period_start=period_start,
        reporting_period_end=period_end,
        total_draw_amount_inr=Decimal(str(calc["total_balance_to_draw_inr"])),
        overall_completion_percentage=Decimal(str(calc["overall_completion_pct"])),
        bank_format=bank_format,
        status="draft",
        generated_by=generated_by,
    )
    db.session.add(report)
    db.session.flush()   # populate report.id before line items

    for it in calc["line_items"]:
        db.session.add(DrawReportLineItem(
            draw_report_id=report.id,
            cost_head_id=it["cost_head_id"],
            budgeted_amount_inr=Decimal(str(it["budgeted_amount_inr"])),
            spent_to_date_inr=Decimal(str(it["spent_to_date_inr"])),
            completion_percentage=Decimal(str(it["completion_percentage"])),
            draw_amount_due_inr=Decimal(str(it["draw_amount_due_inr"])),
            previously_drawn_inr=Decimal(str(it["previously_drawn_inr"])),
            balance_to_draw_inr=Decimal(str(it["balance_to_draw_inr"])),
        ))

    db.session.commit()
    return report


# ── Serialisation helper ──────────────────────────────────────────────────────

def _line_item_dict(li: DrawReportLineItem) -> dict:
    head = li.cost_head
    return {
        "id":                   str(li.id),
        "cost_head_id":         str(li.cost_head_id),
        "category":             head.category if head else None,
        "name":                 head.name if head else None,
        "budgeted_amount_inr":  float(li.budgeted_amount_inr or 0),
        "spent_to_date_inr":    float(li.spent_to_date_inr or 0),
        "completion_percentage": float(li.completion_percentage or 0),
        "draw_amount_due_inr":  float(li.draw_amount_due_inr or 0),
        "previously_drawn_inr": float(li.previously_drawn_inr or 0),
        "balance_to_draw_inr":  float(li.balance_to_draw_inr or 0),
    }


def report_to_dict(report: DrawReport) -> dict:
    return {
        "id":                          str(report.id),
        "project_id":                  str(report.project_id),
        "report_date":                 report.report_date.isoformat(),
        "reporting_period_start":      report.reporting_period_start.isoformat() if report.reporting_period_start else None,
        "reporting_period_end":        report.reporting_period_end.isoformat() if report.reporting_period_end else None,
        "total_draw_amount_inr":       float(report.total_draw_amount_inr or 0),
        "overall_completion_percentage": float(report.overall_completion_percentage or 0),
        "bank_format":                 report.bank_format,
        "status":                      report.status,
        "generated_by":                report.generated_by,
        "created_at":                  report.created_at.isoformat(),
        "line_items": [_line_item_dict(li) for li in report.line_items.all()],
    }
