"""
Project health score: weighted cost + schedule variance → 0–100.

Components
──────────
  Cost     (40 %)  Penalty for overrun (actual > budget) and at-risk (>85 %) heads.
  Schedule (60 %)  SPI-base minus overdue-severity penalty.

Hard caps
─────────
  max single milestone overdue ≥  90 d → score capped at 65
  max single milestone overdue ≥ 180 d → score capped at 40

Grades
──────
  80–100  healthy
  60– 79  watch
  40– 59  at_risk
   0– 39  critical

All weights / thresholds are named constants — easy to tune.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func

from ..extensions import db
from ..models.contractor import Contractor
from ..models.cost_head import CostHead
from ..models.milestone import Milestone
from ..models.transaction import Transaction

# ── Tunable constants ─────────────────────────────────────────────────────────

WEIGHT_COST     = 0.40
WEIGHT_SCHEDULE = 0.60

COST_OVERRUN_RATE  = 200.0   # pts lost per 100 % overrun  (2 pts per 1 %)
COST_AT_RISK_MAX   = 10.0    # max pts lost when utilisation is 85 – 100 %
COST_AT_RISK_FLOOR = 0.85    # utilisation threshold for at-risk zone

SCHED_OVERDUE_PER_MILESTONE = 15.0   # pts per overdue milestone
SCHED_OVERDUE_PER_DAY       =  0.20  # pts per average overdue day
SCHED_OVERDUE_CAP           = 60.0   # max schedule penalty from overdue
SCHED_SPI_DEFAULT           = 50.0   # assumed score when no milestones are due yet

CAP_SEVERE   = 65.0   # cap when max_overdue ≥  90 d
CAP_CRITICAL = 40.0   # cap when max_overdue ≥ 180 d
THRESHOLD_SEVERE   =  90
THRESHOLD_CRITICAL = 180

GRADE_HEALTHY  = 80
GRADE_WATCH    = 60
GRADE_AT_RISK  = 40


# ── Internal helpers ──────────────────────────────────────────────────────────

def _cost_component(project_id) -> tuple[float, dict]:
    """
    Returns (cost_score 0–100, detail_dict).

    Penalises:
      - actual > budget  →  (utilisation - 1.0) * COST_OVERRUN_RATE
      - 0.85 ≤ actual/budget < 1.0  →  up to COST_AT_RISK_MAX pts
    """
    actuals: dict[str, Decimal] = {
        str(r.cost_head_id): Decimal(str(r.total))
        for r in db.session.query(
            Transaction.cost_head_id,
            func.coalesce(func.sum(Transaction.amount_inr), 0).label("total"),
        )
        .filter(Transaction.project_id == project_id)
        .group_by(Transaction.cost_head_id)
        .all()
    }

    heads = CostHead.query.filter_by(project_id=project_id).all()
    if not heads:
        return 100.0, {"reason": "no_cost_heads"}

    total_budget = sum(h.budgeted_amount_inr or Decimal("0") for h in heads)
    total_actual = sum(actuals.get(str(h.id), Decimal("0")) for h in heads)

    overrun_heads: list[str] = []
    at_risk_heads: list[str] = []
    penalty = 0.0

    for h in heads:
        budget = float(h.budgeted_amount_inr or 0)
        actual = float(actuals.get(str(h.id), Decimal("0")))
        if budget <= 0:
            continue
        u = actual / budget
        if u > 1.0:
            penalty += (u - 1.0) * COST_OVERRUN_RATE
            overrun_heads.append(h.category)
        elif u > COST_AT_RISK_FLOOR:
            # Linear ramp from 0 at threshold to COST_AT_RISK_MAX at 100 %
            fraction = (u - COST_AT_RISK_FLOOR) / (1.0 - COST_AT_RISK_FLOOR)
            penalty += fraction * COST_AT_RISK_MAX
            at_risk_heads.append(h.category)

    cost_score = max(0.0, 100.0 - penalty)
    detail = {
        "total_budgeted_inr":  float(total_budget),
        "total_actual_inr":    float(total_actual),
        "utilisation_pct":     round(float(total_actual / total_budget * 100), 1) if total_budget else None,
        "penalty_pts":         round(penalty, 2),
        "overrun_heads":       overrun_heads,
        "at_risk_heads":       at_risk_heads,
    }
    return round(cost_score, 2), detail


def _schedule_component(project_id) -> tuple[float, dict]:
    """
    Returns (schedule_score 0–100, detail_dict).

    schedule_score = spi_base − overdue_penalty
      spi_base      = SPI * 100  (SPI = completed / milestones_due_by_today)
      overdue_penalty = min(CAP, count * PER_MILESTONE + avg_days * PER_DAY)
    """
    today = date.today()
    milestones = Milestone.query.filter_by(project_id=project_id).all()
    if not milestones:
        return 100.0, {"reason": "no_milestones"}

    completed = 0
    expected_by_today = 0
    overdue_list: list[tuple[str, int]] = []   # (name, days_overdue)

    for m in milestones:
        is_complete = bool(m.actual_date) or m.completion_percentage >= 100

        if m.planned_date <= today:
            expected_by_today += 1
            if is_complete:
                completed += 1
            else:
                days_overdue = (today - m.planned_date).days
                overdue_list.append((m.name, days_overdue))

    spi = (completed / expected_by_today) if expected_by_today > 0 else None
    spi_base = (spi * 100) if spi is not None else SCHED_SPI_DEFAULT

    overdue_count = len(overdue_list)
    avg_overdue = (
        sum(d for _, d in overdue_list) / overdue_count if overdue_count else 0
    )
    max_overdue = max((d for _, d in overdue_list), default=0)

    overdue_penalty = min(
        SCHED_OVERDUE_CAP,
        overdue_count * SCHED_OVERDUE_PER_MILESTONE + avg_overdue * SCHED_OVERDUE_PER_DAY,
    )
    schedule_score = max(0.0, spi_base - overdue_penalty)

    detail = {
        "total_milestones":  len(milestones),
        "completed":         completed,
        "expected_by_today": expected_by_today,
        "overdue_count":     overdue_count,
        "avg_overdue_days":  round(avg_overdue, 1),
        "max_overdue_days":  max_overdue,
        "spi":               round(spi, 3) if spi is not None else None,
        "spi_base":          round(spi_base, 2),
        "overdue_penalty":   round(overdue_penalty, 2),
        "overdue_milestones": [
            {"name": n, "days_overdue": d} for n, d in sorted(overdue_list, key=lambda x: -x[1])
        ],
    }
    return round(schedule_score, 2), detail


# ── Public API ────────────────────────────────────────────────────────────────

def compute_health_score(project_id) -> dict:
    """
    Compute and return the full health score dict for a project.

    Expects to be called inside an active Flask app context with DB access.
    Does NOT commit anything — read-only.
    """
    today = date.today()

    cost_score, cost_detail     = _cost_component(project_id)
    sched_score, sched_detail   = _schedule_component(project_id)

    raw_score = WEIGHT_COST * cost_score + WEIGHT_SCHEDULE * sched_score

    # Hard caps for critical overdue milestones
    max_overdue = sched_detail.get("max_overdue_days", 0)
    if max_overdue >= THRESHOLD_CRITICAL:
        raw_score = min(raw_score, CAP_CRITICAL)
    elif max_overdue >= THRESHOLD_SEVERE:
        raw_score = min(raw_score, CAP_SEVERE)

    score = round(raw_score, 1)

    if score >= GRADE_HEALTHY:
        grade = "healthy"
    elif score >= GRADE_WATCH:
        grade = "watch"
    elif score >= GRADE_AT_RISK:
        grade = "at_risk"
    else:
        grade = "critical"

    # Human-readable flags for the worst issues
    flags: list[str] = []
    for item in sched_detail.get("overdue_milestones", []):
        flags.append(f"{item['name']} is {item['days_overdue']} days overdue")
    for cat in cost_detail.get("overrun_heads", []):
        flags.append(f"{cat} cost head is over budget")
    for cat in cost_detail.get("at_risk_heads", []):
        flags.append(f"{cat} cost head is above 85 % utilisation")

    return {
        "project_id":  str(project_id),
        "as_of_date":  today.isoformat(),
        "score":       score,
        "grade":       grade,
        "components": {
            "cost": {
                "score":        cost_score,
                "weight":       WEIGHT_COST,
                "contribution": round(WEIGHT_COST * cost_score, 2),
                "detail":       cost_detail,
            },
            "schedule": {
                "score":        sched_score,
                "weight":       WEIGHT_SCHEDULE,
                "contribution": round(WEIGHT_SCHEDULE * sched_score, 2),
                "detail":       sched_detail,
            },
        },
        "flags": flags,
    }
