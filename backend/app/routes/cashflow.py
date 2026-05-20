"""
Cash-flow endpoints.

GET /api/v1/projects/<id>/cashflow
  ?k=<float>    S-curve steepness  (default 8.0)
  ?mid=<float>  S-curve inflection fraction  (default 0.45)

All routes require X-Organisation-Id header.
"""
from flask import Blueprint, jsonify, request

from ..models.project import Project
from ..services.cashflow import DEFAULT_K, DEFAULT_MID, build_cashflow

bp = Blueprint("cashflow", __name__, url_prefix="/api/v1")


def _org_id() -> str:
    org = request.headers.get("X-Organisation-Id", "").strip()
    if not org:
        raise ValueError("X-Organisation-Id header is required")
    return org


@bp.get("/projects/<project_id>/cashflow")
def project_cashflow(project_id: str):
    """
    Returns the full cash-flow series for a project:
      - projected_cumulative_inr  (S-curve × total budget)
      - actual_cumulative_inr     (cumulative transactions)
      - disbursed_cumulative_inr  (cumulative bank disbursements)
      - variance_inr              (actual − projected; negative = behind plan)
      - is_forecast               true for months after today

    Optional query params let the caller adjust the S-curve shape:
      k    steepness  (default 8)
      mid  inflection point as fraction of total duration  (default 0.45)
    """
    try:
        org_id = _org_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    project = Project.query.filter_by(
        id=project_id, organisation_id=org_id
    ).first_or_404()

    try:
        k   = float(request.args.get("k",   DEFAULT_K))
        mid = float(request.args.get("mid", DEFAULT_MID))
    except (TypeError, ValueError):
        return jsonify({"error": "k and mid must be numbers"}), 400

    if not (1.0 <= k <= 20.0):
        return jsonify({"error": "k must be between 1 and 20"}), 400
    if not (0.1 <= mid <= 0.9):
        return jsonify({"error": "mid must be between 0.1 and 0.9"}), 400

    result = build_cashflow(project, k=k, mid=mid)
    if "error" in result:
        return jsonify(result), 422

    return jsonify(result)
