from flask import Blueprint, jsonify, request

from ..services.csv_ingestion import ingest_csv

bp = Blueprint("transactions", __name__, url_prefix="/api/v1")

ALLOWED_EXTENSIONS = {".csv"}
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB


def _get_organisation_id() -> str:
    """
    Temporary: read org from header. Replace with JWT decode once auth is built.
    Header:  X-Organisation-Id: <uuid>
    """
    org_id = request.headers.get("X-Organisation-Id")
    if not org_id:
        raise ValueError("Missing X-Organisation-Id header")
    return org_id


@bp.route("/projects/<project_id>/import/csv", methods=["POST"])
def import_csv(project_id: str):
    """
    POST /api/v1/projects/:id/import/csv
    Body: multipart/form-data  field name: "file"
    Accepts: cost-sheet CSV or Tally export CSV.
    Returns: {format, imported, skipped, errors}
    """
    try:
        org_id = _get_organisation_id()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 401

    if "file" not in request.files:
        return jsonify({"error": "No file provided. Send a multipart field named 'file'."}), 400

    file = request.files["file"]
    if not file.filename or not file.filename.lower().endswith(".csv"):
        return jsonify({"error": "Only .csv files are accepted"}), 400

    file_bytes = file.read()
    if len(file_bytes) > MAX_FILE_BYTES:
        return jsonify({"error": "File exceeds 10 MB limit"}), 413

    try:
        result = ingest_csv(
            file_bytes=file_bytes,
            project_id=project_id,
            organisation_id=org_id,
        )
        return jsonify(result), 200
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"Ingestion failed: {exc}"}), 500


@bp.route("/projects/<project_id>/import/tally", methods=["POST"])
def import_tally(project_id: str):
    """
    POST /api/v1/projects/:id/import/tally
    Body: multipart/form-data  field name: "file"
    Accepts: Tally Excel export (.xlsx) or CSV.
    Returns: {format, imported, skipped, errors}
    """
    try:
        org_id = _get_organisation_id()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 401

    if "file" not in request.files:
        return jsonify({"error": "No file provided. Send a multipart field named 'file'."}), 400

    file = request.files["file"]
    filename = (file.filename or "").lower()

    if filename.endswith(".xlsx") or filename.endswith(".xls"):
        try:
            import pandas as pd
            import io
            df = pd.read_csv(io.BytesIO(file.read()), dtype=str) if filename.endswith(".csv") \
                else pd.read_excel(file, dtype=str)
        except Exception as exc:
            return jsonify({"error": f"Could not parse file: {exc}"}), 400
    elif filename.endswith(".csv"):
        file_bytes = file.read()
        try:
            result = ingest_csv(
                file_bytes=file_bytes,
                project_id=project_id,
                organisation_id=org_id,
            )
            return jsonify(result), 200
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    else:
        return jsonify({"error": "Only .csv or .xlsx files are accepted"}), 400

    from ..services.tally_parser import parse_tally_df, is_tally_format
    from ..models.project import Project
    from ..extensions import db

    if not is_tally_format(df):
        return jsonify({"error": "File does not appear to be a Tally export"}), 400

    project = Project.query.filter_by(
        id=project_id, organisation_id=org_id
    ).first_or_404()

    try:
        df = df.dropna(how="all")
        transactions, errors = parse_tally_df(df, project)
        db.session.add_all(transactions)
        db.session.commit()
        return jsonify({
            "format": "tally",
            "imported": len(transactions),
            "skipped": len(errors),
            "errors": errors,
        }), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": f"Ingestion failed: {exc}"}), 500
