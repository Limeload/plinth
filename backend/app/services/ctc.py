"""
Cost-to-Complete (CTC) forecast service.

Two complementary methods:

1. EVM (Earned Value Management) — per cost head
   ─────────────────────────────────────────────
   EV  = BAC × (completion_pct / 100)
   CPI = EV / AC                         (cost performance index)
   EAC = BAC / CPI                       (estimate at completion)
   CTC = max(0, EAC − AC)               (remaining spend projected)
   VAR = BAC − EAC                       (positive = under budget)

   When completion_pct is spend-based (default), EV = AC and CPI = 1.0,
   so EAC = BAC and CTC = remaining budget. The interesting divergence appears
   when a site engineer enters a manual % complete that differs from spend %.

2. Time-based burn rate — project level
   ─────────────────────────────────────
   burn_rate = recent monthly avg spend (3-month window, ignoring zero-spend months)
   time_ctc  = burn_rate × months_remaining
   time_efc  = total_spent + time_ctc   (estimate final cost)

   "months_to_finish_at_current_rate" = EVM CTC / burn_rate
   A large ratio vs months_remaining flags that the project cannot physically
   finish on time at the current spend rate.
"""
from __future__ import annotations

import math
from calendar import monthrange
from datetime import date
from typing import Generator

from sqlalchemy import extract, func

from ..extensions import db
from ..models.cost_head import CostHead
from ..models.transaction import Transaction


# ── Status thresholds ─────────────────────────────────────────────────────────
#  EAC/BAC > 1.05  → overrun   (more than 5% over budget)
#  EAC/BAC > 0.95  → at_risk   (within 5% of budget)
#  otherwise       → on_track
#  completion ≥ 100 → complete

# EAC/BAC > 1.05  → overrun  (more than 5% projected over budget)
# EAC/BAC > 1.00  → at_risk  (projected to use full budget, could tip over)
# otherwise       → on_track (under budget trajectory)
EAC_OVERRUN_THRESHOLD = 1.05
EAC_AT_RISK_THRESHOLD = 1.00


# ── Burn-rate helpers ─────────────────────────────────────────────────────────

def _monthly_spend_series(project_id, as_of: date) -> list[float]:
    """Return monthly totals (oldest first) up to as_of."""
    rows = (
        db.session.query(
            extract("year",  Transaction.transaction_date).label("yr"),
            extract("month", Transaction.transaction_date).label("mo"),
            func.sum(Transaction.amount_inr).label("total"),
        )
        .filter(
            Transaction.project_id == project_id,
            Transaction.transaction_date <= as_of,
        )
        .group_by("yr", "mo")
        .order_by("yr", "mo")
        .all()
    )
    return [float(r.total) for r in rows]


def _rolling_burn_rate(series: list[float], window: int = 3) -> float:
    """
    Average of the last `window` non-zero months in `series`.
    Falls back to all-time average if fewer than `window` non-zero months exist.
    """
    non_zero = [v for v in series if v > 0]
    if not non_zero:
        return 0.0
    recent = non_zero[-window:]
    return sum(recent) / len(recent)


# ── CPI / EAC per cost head ───────────────────────────────────────────────────

def _head_evm(
    budget: float,
    actual: float,
    completion_pct: float,
) -> dict:
    """
    Returns EVM fields for a single cost head.
    Handles edge cases:
      - pct=0, AC>0  → CPI undefined; use conservative EAC = BAC (unknown trajectory)
      - pct≥100      → complete; EAC = AC, CTC = 0
      - AC=0         → nothing spent; EAC = BAC (linear assumption)
    """
    pct = max(0.0, min(100.0, completion_pct))

    if pct >= 100.0:
        ev  = float(budget)
        cpi = (ev / actual) if actual > 0 else 1.0
        eac = actual if actual > 0 else budget
        ctc = 0.0
    elif actual <= 0:
        # Nothing spent yet — project linearly from budget
        ev  = budget * (pct / 100.0)
        cpi = 1.0
        eac = float(budget)
        ctc = float(budget)
    elif pct <= 0:
        # Spending has started but 0% progress logged — can't compute CPI
        ev  = 0.0
        cpi = None
        eac = float(budget)   # conservative: assume full budget still needed
        ctc = max(0.0, eac - actual)
    else:
        ev  = budget * (pct / 100.0)
        cpi = ev / actual
        # Guard against absurd EAC when CPI is tiny (early-spend artefact)
        eac = min(budget / cpi, budget * 3.0)
        ctc = max(0.0, eac - actual)

    variance     = budget - eac
    variance_pct = (variance / budget * 100) if budget > 0 else 0.0

    # Round to nearest rupee before comparing to avoid IEEE-754 drift
    eac_r = round(eac)
    bac_r = round(budget)
    if pct >= 100.0:
        status = "complete"
    elif eac_r > bac_r * EAC_OVERRUN_THRESHOLD:
        status = "overrun"
    elif eac_r > bac_r * EAC_AT_RISK_THRESHOLD:
        status = "at_risk"
    else:
        status = "on_track"

    return {
        "earned_value_inr":   round(ev),
        "cpi":                round(cpi, 3) if cpi is not None else None,
        "eac_inr":            round(eac),
        "ctc_inr":            round(ctc),
        "budget_variance_inr":round(variance),
        "variance_pct":       round(variance_pct, 1),
        "status":             status,
    }


# ── Main service function ─────────────────────────────────────────────────────

def compute_ctc(
    project,
    as_of: date | None = None,
    completion_overrides: dict[str, float] | None = None,
) -> dict:
    """
    Compute the Cost-to-Complete forecast for a project.

    project              — Project ORM instance
    as_of                — cut-off date (defaults to today)
    completion_overrides — {cost_head_id: pct} to override spend-based completion

    Returns a dict with:
      summary         — project-level EVM totals + time-based projection
      line_items      — per cost-head breakdown
    """
    as_of     = as_of or date.today()
    overrides = completion_overrides or {}

    heads = (
        CostHead.query.filter_by(project_id=project.id)
        .order_by(CostHead.category)
        .all()
    )
    if not heads:
        return {"error": "Project has no cost heads"}

    # Actual spend per cost head up to as_of
    actuals: dict[str, float] = {
        str(r.cost_head_id): float(r.total)
        for r in (
            db.session.query(
                Transaction.cost_head_id,
                func.sum(Transaction.amount_inr).label("total"),
            )
            .filter(
                Transaction.project_id == project.id,
                Transaction.transaction_date <= as_of,
            )
            .group_by(Transaction.cost_head_id)
            .all()
        )
    }

    # ── Per-head EVM ──────────────────────────────────────────────────────────

    line_items: list[dict] = []
    total_budget = 0.0
    total_spent  = 0.0
    total_ev     = 0.0
    total_eac    = 0.0

    for h in heads:
        budget = float(h.budgeted_amount_inr or 0)
        actual = actuals.get(str(h.id), 0.0)

        if str(h.id) in overrides:
            completion_pct    = max(0.0, min(100.0, float(overrides[str(h.id)])))
            completion_source = "manual"
        else:
            completion_pct    = min(actual / budget * 100.0, 100.0) if budget > 0 else 0.0
            completion_source = "spend"

        evm = _head_evm(budget, actual, completion_pct)

        line_items.append({
            "cost_head_id":         str(h.id),
            "category":             h.category,
            "name":                 h.name,
            "budgeted_inr":         round(budget),
            "spent_to_date_inr":    round(actual),
            "completion_pct":       round(completion_pct, 2),
            "completion_source":    completion_source,
            **evm,
        })

        total_budget += budget
        total_spent  += actual
        total_ev     += evm["earned_value_inr"]
        total_eac    += evm["eac_inr"]

    # ── Project-level EVM summary ─────────────────────────────────────────────

    project_ctc      = max(0.0, total_eac - total_spent)
    project_variance = total_budget - total_eac
    project_cpi      = (total_ev / total_spent) if total_spent > 0 else 1.0

    overall_completion = (
        sum(it["completion_pct"] * it["budgeted_inr"] for it in line_items)
        / total_budget
        if total_budget > 0 else 0.0
    )

    # ── Time-based burn-rate projection ───────────────────────────────────────

    start = project.start_date
    end   = project.expected_completion_date
    months_elapsed   = max(1.0, (as_of - start).days / 30.44) if start else 1.0
    months_remaining = max(0.0, (end - as_of).days / 30.44)    if end   else 0.0

    series           = _monthly_spend_series(project.id, as_of)
    burn_3m          = _rolling_burn_rate(series, window=3)
    burn_alltime     = total_spent / months_elapsed

    time_ctc         = burn_3m * months_remaining
    time_efc         = total_spent + time_ctc          # Estimate Final Cost
    time_variance    = total_budget - time_efc

    # How many months does the EVM CTC imply at current burn rate?
    months_to_finish = (project_ctc / burn_3m) if burn_3m > 0 else None

    schedule_risk = (
        months_to_finish is not None
        and months_remaining > 0
        and months_to_finish > months_remaining * 1.2
    )

    return {
        "project_id":   str(project.id),
        "project_name": project.name,
        "as_of_date":   as_of.isoformat(),
        "summary": {
            "total_budget_inr":       round(total_budget),
            "total_spent_inr":        round(total_spent),
            "total_eac_inr":          round(total_eac),
            "total_ctc_inr":          round(project_ctc),
            "total_variance_inr":     round(project_variance),
            "variance_pct":           round(project_variance / total_budget * 100, 1) if total_budget else None,
            "project_cpi":            round(project_cpi, 3),
            "overall_completion_pct": round(overall_completion, 2),
            "time_based": {
                "months_elapsed":            round(months_elapsed, 1),
                "months_remaining":          round(months_remaining, 1),
                "burn_rate_3m_inr":          round(burn_3m),
                "burn_rate_alltime_inr":     round(burn_alltime),
                "time_ctc_inr":              round(time_ctc),
                "time_efc_inr":              round(time_efc),
                "time_variance_inr":         round(time_variance),
                "months_to_finish_at_burn":  round(months_to_finish, 1) if months_to_finish is not None else None,
                "schedule_risk":             schedule_risk,
            },
        },
        "line_items": line_items,
    }
