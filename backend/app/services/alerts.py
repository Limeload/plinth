"""
Overrun alert service.

Four alert types, checked per cost head:

  overrun_risk  spend > OVERRUN_SPEND_PCT of budget
                AND project milestone completion < OVERRUN_MILESTONE_PCT
                → the category is burning budget faster than physical progress warrants

  over_budget   spend ≥ 100 % of budget
                → already exceeded

  near_limit    spend ≥ NEAR_LIMIT_PCT of budget (but < 100 %)
                → close to ceiling, no overrun yet

  pace_risk     at current 3-month burn rate, remaining budget exhausted
                in < months_remaining × PACE_RISK_BUFFER
                → on track to blow the head's budget before the project ends

All thresholds are named constants so tuning doesn't require changing logic.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import extract, func

from ..extensions import db
from ..models.cost_head import CostHead
from ..models.milestone import Milestone
from ..models.project import Project
from ..models.transaction import Transaction


# ── Tunable thresholds ────────────────────────────────────────────────────────

OVERRUN_SPEND_PCT      = 0.80   # cost head > 80 % of budget
OVERRUN_MILESTONE_PCT  = 60.0   # project milestone completion < 60 %
NEAR_LIMIT_PCT         = 0.90   # cost head > 90 % of budget
PACE_RISK_BUFFER       = 1.0    # months_to_exhaust < months_remaining × buffer


# ── Internal helpers ──────────────────────────────────────────────────────────

def _project_milestone_completion(project_id) -> float:
    """
    Returns 0–100: percentage of milestones that are complete.
    Complete = actual_date set OR completion_percentage ≥ 100.
    """
    milestones = Milestone.query.filter_by(project_id=project_id).all()
    if not milestones:
        return 0.0
    done = sum(
        1 for m in milestones
        if m.actual_date or float(m.completion_percentage or 0) >= 100
    )
    return done / len(milestones) * 100.0


def _monthly_spend_by_head(project_id) -> dict[str, list[float]]:
    """Returns {cost_head_id: [monthly_total, ...]} oldest-first."""
    rows = (
        db.session.query(
            Transaction.cost_head_id,
            extract("year",  Transaction.transaction_date).label("yr"),
            extract("month", Transaction.transaction_date).label("mo"),
            func.sum(Transaction.amount_inr).label("total"),
        )
        .filter(Transaction.project_id == project_id)
        .group_by(Transaction.cost_head_id, "yr", "mo")
        .order_by(Transaction.cost_head_id, "yr", "mo")
        .all()
    )
    result: dict[str, list[float]] = {}
    for r in rows:
        result.setdefault(str(r.cost_head_id), []).append(float(r.total))
    return result


def _burn_rate_3m(series: list[float]) -> float:
    """Average of the last 3 non-zero months."""
    non_zero = [v for v in series if v > 0]
    if not non_zero:
        return 0.0
    return sum(non_zero[-3:]) / len(non_zero[-3:])


# ── Public API ────────────────────────────────────────────────────────────────

def compute_alerts(project_id) -> dict:
    """
    Compute all active alerts for a project.

    Returns:
      project_id, as_of_date, milestone_completion_pct, months_remaining,
      alerts (list, severity-sorted), counts {critical, warning, info, total}
    """
    today   = date.today()
    project = Project.query.filter_by(id=project_id).first()
    if not project:
        return {"error": "Project not found"}

    milestone_pct    = _project_milestone_completion(project_id)
    months_remaining = (
        max(0.0, (project.expected_completion_date - today).days / 30.44)
        if project.expected_completion_date else 0.0
    )

    actuals: dict[str, float] = {
        str(r.cost_head_id): float(r.total)
        for r in (
            db.session.query(
                Transaction.cost_head_id,
                func.sum(Transaction.amount_inr).label("total"),
            )
            .filter(Transaction.project_id == project_id)
            .group_by(Transaction.cost_head_id)
            .all()
        )
    }

    monthly_by_head = _monthly_spend_by_head(project_id)
    heads           = (
        CostHead.query.filter_by(project_id=project_id)
        .order_by(CostHead.category)
        .all()
    )

    alerts: list[dict] = []

    for h in heads:
        budget = float(h.budgeted_amount_inr or 0)
        if budget <= 0:
            continue

        spent       = actuals.get(str(h.id), 0.0)
        remaining   = max(0.0, budget - spent)
        utilisation = spent / budget

        base = {
            "cost_head_id":             str(h.id),
            "cost_head_category":       h.category,
            "cost_head_name":           h.name,
            "utilisation_pct":          round(utilisation * 100, 1),
            "milestone_completion_pct": round(milestone_pct, 1),
            "budget_inr":               round(budget),
            "spent_inr":                round(spent),
            "remaining_inr":            round(remaining),
        }

        # ── over_budget ───────────────────────────────────────────────────────
        if utilisation >= 1.0:
            alerts.append({
                **base,
                "type":     "over_budget",
                "severity": "critical",
                "message":  (
                    f"{h.category} has exceeded its budget — "
                    f"₹{spent:,.0f} spent of ₹{budget:,.0f} "
                    f"({utilisation*100:.1f}%)"
                ),
            })
            continue   # over_budget subsumes all lower-priority alerts for this head

        # ── near_limit ────────────────────────────────────────────────────────
        if utilisation >= NEAR_LIMIT_PCT:
            alerts.append({
                **base,
                "type":     "near_limit",
                "severity": "warning",
                "message":  (
                    f"{h.category} is at {utilisation*100:.1f}% of budget "
                    f"with ₹{remaining:,.0f} remaining"
                ),
            })
            # fall through to pace_risk — both can fire on the same head

        # ── overrun_risk ──────────────────────────────────────────────────────
        elif utilisation >= OVERRUN_SPEND_PCT and milestone_pct < OVERRUN_MILESTONE_PCT:
            alerts.append({
                **base,
                "type":     "overrun_risk",
                "severity": "warning",
                "message":  (
                    f"{h.category} has used {utilisation*100:.1f}% of its budget "
                    f"but the project is only {milestone_pct:.1f}% complete by milestones"
                ),
            })

        # ── pace_risk — only when time remains and remaining budget to protect ──
        if months_remaining > 1.0 and remaining > 0:
            burn = _burn_rate_3m(monthly_by_head.get(str(h.id), []))
            if burn > 0:
                months_to_exhaust = remaining / burn
                if months_to_exhaust < months_remaining * PACE_RISK_BUFFER:
                    alerts.append({
                        **base,
                        "type":               "pace_risk",
                        "severity":           "info",
                        "burn_rate_3m_inr":   round(burn),
                        "months_to_exhaust":  round(months_to_exhaust, 1),
                        "months_remaining":   round(months_remaining, 1),
                        "message":            (
                            f"{h.category} budget exhausted in "
                            f"{months_to_exhaust:.1f} months at current burn rate "
                            f"({months_remaining:.1f} months remaining on project)"
                        ),
                    })

    _order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: (_order[a["severity"]], a["cost_head_category"]))

    counts = {
        "critical": sum(1 for a in alerts if a["severity"] == "critical"),
        "warning":  sum(1 for a in alerts if a["severity"] == "warning"),
        "info":     sum(1 for a in alerts if a["severity"] == "info"),
        "total":    len(alerts),
    }

    return {
        "project_id":               str(project_id),
        "as_of_date":               today.isoformat(),
        "milestone_completion_pct": round(milestone_pct, 1),
        "months_remaining":         round(months_remaining, 1),
        "alerts":                   alerts,
        "counts":                   counts,
    }
