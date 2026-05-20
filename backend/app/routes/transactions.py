"""
Transaction import endpoints.

POST /api/v1/projects/<id>/import/csv    — cost-sheet CSV (Plinth format)
POST /api/v1/projects/<id>/import/tally  — Tally ERP export (.csv, .xls, .xlsx)

Both endpoints accept multipart/form-data with a "file" field and return:
  {format, imported, skipped_duplicate, errors}

All routes require X-Organisation-Id header.
"""
from flask import Blueprint, jsonify, request

from ..models.project import Project
from ..services.csv_ingestion import ingest_csv
from ..services.tally_parser import ingest_tally_file

bp = Blueprint("transactions", __name__, url_prefix="/api/v1")

MAX_FILE_BYTES    = 10 * 1024 * 1024   # 10 MB
_TALLY_EXTENSIONS = {".csv", ".xls", ".xlsx"}
_CSV_EXTENSIONS   = {".csv"}


def _org_id() -> str:
    org = request.headers.get("X-Organisation-Id", "").strip()
    if not org:
        raise ValueError("X-Organisation-Id header is required")
    return org


def _read_upload(allowed_exts: set[str]) -> tuple[bytes, str]:
    """Validate and read the uploaded file. Returns (file_bytes, filename)."""
    if "file" not in request.files:
        raise ValueError("No file provided — send a multipart field named 'file'")
    f = request.files["file"]
    if not f.filename:
        raise ValueError("File has no name")
    ext = "." + f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in allowed_exts:
        raise ValueError(
            f"Unsupported file type '{ext}'. Accepted: {', '.join(sorted(allowed_exts))}"
        )
    data = f.read()
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("File exceeds 10 MB limit")
    return data, f.filename


# ── Cost-sheet CSV ────────────────────────────────────────────────────────────

@bp.post("/projects/<project_id>/import/csv")
def import_csv(project_id: str):
    """
    POST /api/v1/projects/<id>/import/csv
    Accepts a cost-sheet CSV in Plinth format (date, description, category,
    vendor, amount, invoice_no, gst, tds).

    The same endpoint auto-detects Tally CSV exports and routes them through
    the Tally parser.
    """
    try:
        org_id = _org_id()
        file_bytes, _ = _read_upload(_CSV_EXTENSIONS)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        result = ingest_csv(
            file_bytes=file_bytes,
            project_id=project_id,
            organisation_id=org_id,
        )
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Ingestion failed: {e}"}), 500


# ── Tally export ──────────────────────────────────────────────────────────────

@bp.post("/projects/<project_id>/import/tally")
def import_tally(project_id: str):
    """
    POST /api/v1/projects/<id>/import/tally
    Accepts a Tally ERP voucher export in CSV, XLS, or XLSX format.

    Idempotent: re-uploading the same file skips already-imported rows
    (deduplicated by voucher hash stored as invoice_number).
    """
    try:
        org_id = _org_id()
        file_bytes, filename = _read_upload(_TALLY_EXTENSIONS)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    project = Project.query.filter_by(
        id=project_id, organisation_id=org_id
    ).first_or_404()

    try:
        result = ingest_tally_file(file_bytes, filename, project)
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Ingestion failed: {e}"}), 500
