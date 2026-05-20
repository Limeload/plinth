"""
Cash-flow forecast service.

Generates a monthly series comparing:
  projected_cumulative  — logistic S-curve scaled to total_budget_inr
  actual_cumulative     — cumulative sum of transactions
  disbursed_cumulative  — cumulative sum of bank disbursements

S-curve formula
───────────────
  F(t) = [L(t) - L(0)] / [L(1) - L(0)]
  L(t) = 1 / (1 + exp(-k * (t - mid)))

  t   — elapsed fraction of project duration  (0 at start, 1 at end)
  k   — steepness  (8 = sharp inflection, lower = gentler)
  mid — inflection point as fraction of duration
        0.40-0.45 = front-loaded (typical Indian residential)
        0.50      = symmetric

Default k=8, mid=0.45 matches a project that front-loads structure spend,
then tapers off during finishing and handover.
"""
from __future__ import annotations

import math
from calendar import monthrange
from datetime import date
from typing import Generator

from sqlalchemy import extract, func

from ..extensions import db
from ..models.disbursement import Disbursement
from ..models.transaction import Transaction

DEFAULT_K   = 8.0
DEFAULT_MID = 0.45


# ── S-curve ───────────────────────────────────────────────────────────────────

def _logistic(t: float, k: float, mid: float) -> float:
    return 1.0 / (1.0 + math.exp(-k * (t - mid)))


def s_curve(t: float, k: float = DEFAULT_K, mid: float = DEFAULT_MID) -> float:
    """
    Cumulative spend fraction at project-time fraction t ∈ [0, 1].
    Returns a value in [0, 1] with s_curve(0) == 0 and s_curve(1) == 1.
    """
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    f0 = _logistic(0.0, k, mid)
    f1 = _logistic(1.0, k, mid)
    return (_logistic(t, k, mid) - f0) / (f1 - f0)


# ── Month iterator ────────────────────────────────────────────────────────────

def _month_starts(start: date, end: date) -> Generator[date, None, None]:
    cur = start.replace(day=1)
    cap = end.replace(day=1)
    while cur <= cap:
        yield cur
        cur = (
            cur.replace(year=cur.year + 1, month=1)
            if cur.month == 12
            else cur.replace(month=cur.month + 1)
        )


# ── Main service function ─────────────────────────────────────────────────────

def build_cashflow(project, k: float = DEFAULT_K, mid: float = DEFAULT_MID) -> dict:
    """
    Return the full cash-flow response dict for a project.

    project  — Project ORM instance (must have start_date, expected_completion_date,
                total_budget_inr, sanctioned_loan_inr)
    k, mid   — S-curve shape parameters; accept user overrides from query string
    """
    total_budget = float(project.total_budget_inr or 0)
    start = project.start_date
    end   = project.expected_completion_date
    today = date.today()

    if not start or not end:
        return {"error": "Project is missing start_date or expected_completion_date"}
    if total_budget <= 0:
        return {"error": "Project is missing total_budget_inr"}

    total_days = max(1, (end - start).days + 1)

    # ── Pull monthly actuals in two queries ───────────────────────────────────

    txn_rows = (
        db.session.query(
            extract("year",  Transaction.transaction_date).label("yr"),
            extract("month", Transaction.transaction_date).label("mo"),
            func.sum(Transaction.amount_inr).label("total"),
        )
        .filter(Transaction.project_id == project.id)
        .group_by("yr", "mo")
        .all()
    )
    txn_by_ym: dict[tuple[int, int], float] = {
        (int(r.yr), int(r.mo)): float(r.total) for r in txn_rows
    }

    disb_rows = (
        db.session.query(
            extract("year",  Disbursement.disbursement_date).label("yr"),
            extract("month", Disbursement.disbursement_date).label("mo"),
            func.sum(Disbursement.amount_inr).label("total"),
        )
        .filter(Disbursement.project_id == project.id)
        .group_by("yr", "mo")
        .all()
    )
    disb_by_ym: dict[tuple[int, int], float] = {
        (int(r.yr), int(r.mo)): float(r.total) for r in disb_rows
    }

    # ── Build monthly series ──────────────────────────────────────────────────

    series: list[dict] = []
    cum_actual = 0.0
    cum_disb   = 0.0
    current_proj_cum = 0.0

    for month_start in _month_starts(start, end):
        yr, mo = month_start.year, month_start.month
        last_day  = monthrange(yr, mo)[1]
        month_end = month_start.replace(day=last_day)

        # Time fractions at the boundaries of this month
        t_start = max(0.0, (month_start - start).days / total_days)
        t_end   = min(1.0, ((month_end - start).days + 1) / total_days)

        proj_cum_prev = s_curve(t_start, k, mid) * total_budget
        proj_cum_end  = s_curve(t_end,   k, mid) * total_budget
        proj_monthly  = proj_cum_end - proj_cum_prev

        actual_m = txn_by_ym.get((yr, mo), 0.0)
        disb_m   = disb_by_ym.get((yr, mo), 0.0)
        cum_actual += actual_m
        cum_disb   += disb_m

        is_forecast = month_start > today

        if not is_forecast:
            current_proj_cum = proj_cum_end

        series.append({
            "month":                    f"{yr}-{mo:02d}",
            "month_start":              month_start.isoformat(),
            "projected_cumulative_inr": round(proj_cum_end),
            "projected_monthly_inr":    round(proj_monthly),
            "actual_cumulative_inr":    round(cum_actual),
            "actual_monthly_inr":       round(actual_m),
            "disbursed_cumulative_inr": round(cum_disb),
            "disbursed_monthly_inr":    round(disb_m),
            # negative = spending behind plan; positive = ahead of plan
            "variance_inr":             round(cum_actual - proj_cum_end),
            "time_fraction":            round(t_end, 4),
            "is_forecast":              is_forecast,
        })

    total_spent      = sum(txn_by_ym.values())
    total_disbursed  = sum(disb_by_ym.values())
    spend_variance   = total_spent - current_proj_cum

    # Undraw: sanctioned but not yet disbursed
    sanctioned = float(project.sanctioned_loan_inr or 0)
    undrawn    = max(0.0, sanctioned - total_disbursed)

    return {
        "project_id":            str(project.id),
        "project_name":          project.name,
        "start_date":            start.isoformat(),
        "end_date":              end.isoformat(),
        "total_budget_inr":      total_budget,
        "sanctioned_loan_inr":   sanctioned,
        "bank_name":             project.bank_name,
        "total_spent_inr":       round(total_spent),
        "total_disbursed_inr":   round(total_disbursed),
        "undrawn_sanctioned_inr": round(undrawn),
        "current_projected_inr": round(current_proj_cum),
        "spend_variance_inr":    round(spend_variance),
        "spend_variance_pct":    round(spend_variance / current_proj_cum * 100, 1) if current_proj_cum else None,
        "curve_params": {
            "k":          k,
            "mid":        mid,
            "description": "Logistic S-curve — k=steepness, mid=inflection fraction of duration",
        },
        "series": series,
    }
