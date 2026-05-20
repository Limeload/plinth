"""
Milestone endpoints.

All routes require X-Organisation-Id header (temp auth until JWT middleware).
"""
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from flask import Blueprint, jsonify, request

from ..extensions import db
from ..models.milestone import Milestone
from ..models.project import Project

bp = Blueprint("milestones", __name__, url_prefix="/api/v1")


def _org_id() -> str:
    org = request.headers.get("X-Organisation-Id", "").strip()
    if not org:
        raise ValueError("X-Organisation-Id header is required")
    return org


def _get_project(project_id: str, org_id: str) -> Project:
    proj = Project.query.filter_by(
        id=project_id, organisation_id=org_id
    ).first_or_404(description=f"Project {project_id} not found")
    return proj


def _milestone_dict(m: Milestone) -> dict:
    today = date.today()
    is_overdue = (
        m.actual_date is None
        and m.completion_percentage < 100
        and m.planned_date < today
    )
    variance_days = None
    if m.actual_date and m.planned_date:
        variance_days = (m.actual_date - m.planned_date).days
    elif is_overdue:
        variance_days = (today - m.planned_date).days  # positive = days past due

    return {
        "id":                    str(m.id),
        "project_id":            str(m.project_id),
        "name":                  m.name,
        "phase":                 m.phase,
        "planned_date":          m.planned_date.isoformat(),
        "actual_date":           m.actual_date.isoformat() if m.actual_date else None,
        "completion_percentage": float(m.completion_percentage),
        "is_overdue":            is_overdue,
        "variance_days":         variance_days,
        "notes":                 m.notes,
    }


# ── GET /api/v1/projects/<id>/milestones ─────────────────────────────────────

@bp.get("/projects/<project_id>/milestones")
def list_milestones(project_id: str):
    try:
        org_id = _org_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    _get_project(project_id, org_id)

    phase = request.args.get("phase")
    query = Milestone.query.filter_by(project_id=project_id)
    if phase:
        query = query.filter_by(phase=phase)
    milestones = query.order_by(Milestone.planned_date).all()

    return jsonify([_milestone_dict(m) for m in milestones])


# ── POST /api/v1/projects/<id>/milestones ────────────────────────────────────

@bp.post("/projects/<project_id>/milestones")
def create_milestone(project_id: str):
    try:
        org_id = _org_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    proj = _get_project(project_id, org_id)
    body = request.get_json(silent=True) or {}

    try:
        name = str(body.get("name", "")).strip()
        if not name:
            raise ValueError("name is required")

        planned_raw = body.get("planned_date")
        if not planned_raw:
            raise ValueError("planned_date is required")
        planned = datetime.strptime(str(planned_raw), "%Y-%m-%d").date()

        actual = None
        if body.get("actual_date"):
            actual = datetime.strptime(str(body["actual_date"]), "%Y-%m-%d").date()

        pct = Decimal(str(body.get("completion_percentage", 0)))
        if not (0 <= pct <= 100):
            raise ValueError("completion_percentage must be 0–100")

    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    m = Milestone(
        project_id=proj.id,
        name=name,
        phase=str(body.get("phase", "")).strip() or None,
        planned_date=planned,
        actual_date=actual,
        completion_percentage=pct,
        notes=str(body.get("notes", "")).strip() or None,
    )
    db.session.add(m)
    db.session.commit()
    return jsonify(_milestone_dict(m)), 201


# ── PATCH /api/v1/projects/<id>/milestones/<milestone_id> ────────────────────

@bp.patch("/projects/<project_id>/milestones/<milestone_id>")
def update_milestone(project_id: str, milestone_id: str):
    try:
        org_id = _org_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    _get_project(project_id, org_id)

    m = Milestone.query.filter_by(
        id=milestone_id, project_id=project_id
    ).first_or_404()

    body = request.get_json(silent=True) or {}

    try:
        if "actual_date" in body:
            val = body["actual_date"]
            m.actual_date = datetime.strptime(str(val), "%Y-%m-%d").date() if val else None

        if "completion_percentage" in body:
            pct = Decimal(str(body["completion_percentage"]))
            if not (0 <= pct <= 100):
                raise ValueError("completion_percentage must be 0–100")
            m.completion_percentage = pct

        if "phase" in body:
            m.phase = str(body["phase"]).strip() or None

        if "notes" in body:
            m.notes = str(body["notes"]).strip() or None

        if "planned_date" in body:
            m.planned_date = datetime.strptime(str(body["planned_date"]), "%Y-%m-%d").date()

    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    db.session.commit()
    return jsonify(_milestone_dict(m))


# ── GET /api/v1/projects/<id>/milestones/variance ────────────────────────────

@bp.get("/projects/<project_id>/milestones/variance")
def milestone_variance(project_id: str):
    """
    Returns variance summary:
      - completed_on_time: count of milestones where actual_date <= planned_date
      - completed_late: count where actual_date > planned_date
      - overdue: not complete and planned_date < today
      - on_track: not complete and planned_date >= today
      - avg_delay_days: average (actual - planned) days for late completions
    """
    try:
        org_id = _org_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    _get_project(project_id, org_id)

    milestones = Milestone.query.filter_by(project_id=project_id).all()
    today = date.today()

    on_time = 0
    late = 0
    overdue = 0
    on_track = 0
    delay_days: list[int] = []

    for m in milestones:
        if m.actual_date:
            delta = (m.actual_date - m.planned_date).days
            if delta <= 0:
                on_time += 1
            else:
                late += 1
                delay_days.append(delta)
        elif m.planned_date < today and m.completion_percentage < 100:
            overdue += 1
        else:
            on_track += 1

    return jsonify({
        "project_id":        project_id,
        "total":             len(milestones),
        "completed_on_time": on_time,
        "completed_late":    late,
        "overdue":           overdue,
        "on_track":          on_track,
        "avg_delay_days":    round(sum(delay_days) / len(delay_days), 1) if delay_days else 0,
        "overdue_list":      [
            _milestone_dict(m) for m in milestones
            if m.actual_date is None and m.planned_date < today and m.completion_percentage < 100
        ],
    })


# ── GET /api/v1/projects/<id>/milestones/schedule-variance ───────────────────

def _milestone_status(m: Milestone, today: date) -> str:
    if m.actual_date:
        delta = (m.actual_date - m.planned_date).days
        if delta < 0:
            return "completed_early"
        if delta == 0:
            return "completed_on_time"
        return "completed_late"
    if m.completion_percentage == 100:
        return "completed_on_time"   # marked 100% but no actual_date recorded
    if m.planned_date < today:
        return "overdue"
    if m.completion_percentage == 0:
        return "not_started"
    return "on_track"


def _sv_milestone_row(m: Milestone, today: date) -> dict:
    status = _milestone_status(m, today)
    if m.actual_date:
        variance_days = (m.actual_date - m.planned_date).days
    elif status == "overdue":
        variance_days = (today - m.planned_date).days   # days past due, positive
    else:
        variance_days = None

    return {
        "id":                    str(m.id),
        "name":                  m.name,
        "phase":                 m.phase,
        "planned_date":          m.planned_date.isoformat(),
        "actual_date":           m.actual_date.isoformat() if m.actual_date else None,
        "completion_percentage": float(m.completion_percentage),
        "status":                status,
        "variance_days":         variance_days,
        "notes":                 m.notes,
    }


@bp.get("/projects/<project_id>/milestones/schedule-variance")
def schedule_variance(project_id: str):
    """
    Schedule variance report: planned vs actual milestone dates.

    project_id  — org-scoped project

    Response shape:
      as_of_date            date the report was generated
      summary               project-level schedule health
        total               total milestones
        completed           milestones with actual_date (or 100 %)
        overdue             incomplete milestones past planned_date
        on_track            incomplete milestones with planned_date >= today
        not_started         pct == 0 and planned_date >= today
        spi                 Schedule Performance Index = completed / expected_by_today
                            (milestones that should have been done by today)
                            1.0 = perfect; <1.0 = behind; null if no milestone due yet
        avg_delay_days      mean (actual - planned) for all late completions only
        max_delay_days      worst single-milestone delay (completed)
        avg_overdue_days    mean days past due for currently overdue milestones
        projected_end_date  last planned_date shifted by avg_delay_days (null if no delays)
      phases[]              per-phase rollup (ordered by earliest planned_date in phase)
        phase
        total / completed / overdue / on_track / not_started
        completion_pct      (completed / total) * 100
        avg_delay_days      for completed late milestones in this phase
        status              complete | delayed | in_progress | not_started
      milestones[]          per-milestone rows, ordered by planned_date
    """
    try:
        org_id = _org_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    _get_project(project_id, org_id)

    today = date.today()
    all_ms = (
        Milestone.query.filter_by(project_id=project_id)
        .order_by(Milestone.planned_date)
        .all()
    )

    # ── Per-milestone rows and project-level accumulators ────────────────────
    milestone_rows = []
    total = completed = overdue = on_track = not_started = 0
    completed_late_delays: list[int] = []   # (actual - planned).days for late
    overdue_delays: list[int] = []          # (today - planned).days for overdue
    expected_by_today = 0                   # milestones whose planned_date <= today
    last_planned_date: date | None = None

    for m in all_ms:
        total += 1
        status = _milestone_status(m, today)
        row = _sv_milestone_row(m, today)
        milestone_rows.append(row)

        if m.planned_date <= today:
            expected_by_today += 1

        if m.planned_date > last_planned_date if last_planned_date else True:
            last_planned_date = m.planned_date

        if status in ("completed_early", "completed_on_time", "completed_late"):
            completed += 1
            if status == "completed_late" and m.actual_date:
                completed_late_delays.append((m.actual_date - m.planned_date).days)
        elif status == "overdue":
            overdue += 1
            overdue_delays.append((today - m.planned_date).days)
        elif status == "not_started":
            not_started += 1
            on_track += 1   # not_started is a subset of on_track
        else:
            on_track += 1   # "on_track" proper

    # SPI: completed milestones / milestones expected complete by today
    spi = round(completed / expected_by_today, 2) if expected_by_today > 0 else None

    avg_delay = round(sum(completed_late_delays) / len(completed_late_delays), 1) if completed_late_delays else 0
    max_delay = max(completed_late_delays, default=0)
    avg_overdue = round(sum(overdue_delays) / len(overdue_delays), 1) if overdue_delays else 0

    projected_end: str | None = None
    if avg_delay > 0 and last_planned_date:
        projected_end = (last_planned_date + timedelta(days=int(avg_delay))).isoformat()

    # ── Per-phase rollup ─────────────────────────────────────────────────────
    phase_buckets: dict[str, list[Milestone]] = defaultdict(list)
    for m in all_ms:
        phase_buckets[m.phase or "—"].append(m)

    phases = []
    for phase_name, ms in phase_buckets.items():
        p_total = len(ms)
        p_completed = p_late = p_overdue = p_on_track = p_not_started = 0
        p_delays: list[int] = []
        earliest_planned = min(m.planned_date for m in ms)

        for m in ms:
            st = _milestone_status(m, today)
            if st in ("completed_early", "completed_on_time", "completed_late"):
                p_completed += 1
                if st == "completed_late" and m.actual_date:
                    p_delays.append((m.actual_date - m.planned_date).days)
                    p_late += 1
            elif st == "overdue":
                p_overdue += 1
            elif st == "not_started":
                p_not_started += 1
                p_on_track += 1
            else:
                p_on_track += 1

        if p_completed == p_total:
            phase_status = "complete"
        elif p_overdue > 0:
            phase_status = "delayed"
        elif p_completed > 0 or any(
            _milestone_status(m, today) == "on_track" and m.completion_percentage > 0
            for m in ms
        ):
            phase_status = "in_progress"
        else:
            phase_status = "not_started"

        phases.append({
            "phase":            phase_name,
            "earliest_planned": earliest_planned.isoformat(),
            "total":            p_total,
            "completed":        p_completed,
            "completed_late":   p_late,
            "overdue":          p_overdue,
            "on_track":         p_on_track,
            "not_started":      p_not_started,
            "completion_pct":   round(p_completed / p_total * 100, 1),
            "avg_delay_days":   round(sum(p_delays) / len(p_delays), 1) if p_delays else 0,
            "status":           phase_status,
        })

    phases.sort(key=lambda p: p["earliest_planned"])

    return jsonify({
        "project_id": project_id,
        "as_of_date": today.isoformat(),
        "summary": {
            "total":               total,
            "completed":           completed,
            "overdue":             overdue,
            "on_track":            on_track,
            "not_started":         not_started,
            "spi":                 spi,
            "avg_delay_days":      avg_delay,
            "max_delay_days":      max_delay,
            "avg_overdue_days":    avg_overdue,
            "projected_end_date":  projected_end,
        },
        "phases":     phases,
        "milestones": milestone_rows,
    })
