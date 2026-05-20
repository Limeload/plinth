"""
Parses cost-sheet CSV exports (from Excel) into Transaction records.

Expected columns (case-insensitive, order flexible):
  date, description, category, vendor, amount, invoice_no, gst, tds

Also serves as the entry-point router — delegates Tally-format CSVs to tally_parser.
"""
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

import pandas as pd

from ..extensions import db
from ..models.cost_head import CostHead
from ..models.project import Project
from ..models.transaction import Transaction
from .normaliser import resolve_best
from .tally_parser import is_tally_format, parse_tally_df

CATEGORY_TO_TXN_TYPE: dict[str, str] = {
    "Civil Structure": "material_purchase",
    "MEP": "material_purchase",
    "Finishing": "material_purchase",
    "External Development": "material_purchase",
    "Labour": "labour",
    "Equipment": "material_purchase",
    "Misc": "petty_cash",
}

# Columns we try to detect (each field has multiple candidate names)
COLUMN_CANDIDATES: dict[str, list[str]] = {
    "date":        ["date", "transaction_date", "voucher_date", "bill_date", "invoice_date"],
    "description": ["description", "particulars", "narration", "details", "item"],
    "category":    ["category", "cost_head", "head", "type", "cost_category"],
    "vendor":      ["vendor", "vendor_name", "party_name", "supplier", "party"],
    "amount":      ["amount", "amount_inr", "debit_amount", "debit", "value", "net_amount"],
    "invoice_no":  ["invoice_no", "invoice_number", "voucher_no", "bill_no", "ref_no", "reference"],
    "gst":         ["gst", "gst_amount", "gst_amount_inr", "cgst_sgst", "tax_amount"],
    "tds":         ["tds", "tds_amount", "tds_amount_inr", "tds_deducted"],
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_columns(df: pd.DataFrame) -> dict[str, str]:
    """Map our field names to actual column names in the DataFrame."""
    normalised = {c.strip().lower().replace(" ", "_"): c for c in df.columns}
    resolved: dict[str, str] = {}
    for field, candidates in COLUMN_CANDIDATES.items():
        for candidate in candidates:
            if candidate in normalised:
                resolved[field] = normalised[candidate]
                break
    return resolved


def _get_or_create_cost_head(project: Project, category: str) -> CostHead:
    """Return existing CostHead for category, or create a zero-budget placeholder."""
    cost_head = CostHead.query.filter_by(
        project_id=project.id, category=category
    ).first()
    if cost_head:
        return cost_head
    cost_head = CostHead(
        project_id=project.id,
        name=category,
        category=category,
        budgeted_amount_inr=Decimal("0"),
    )
    db.session.add(cost_head)
    db.session.flush()
    return cost_head


def _parse_date(raw) -> date:
    """Parse Indian and ISO date formats."""
    raw = str(raw).strip()
    formats = (
        "%d-%m-%Y", "%d/%m/%Y",
        "%d-%m-%y", "%d/%m/%y",
        "%d-%b-%Y", "%d-%b-%y",
        "%Y-%m-%d", "%m/%d/%Y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date format: {raw!r}")


def _parse_amount(raw) -> Decimal:
    """Parse amounts with Indian formatting (commas, ₹, spaces)."""
    if pd.isna(raw) or str(raw).strip() in ("", "-", "nil", "n/a"):
        return Decimal("0")
    cleaned = (
        str(raw)
        .replace(",", "")
        .replace("₹", "")
        .replace("Rs.", "")
        .replace("Rs", "")
        .replace(" ", "")
        .strip()
    )
    try:
        return Decimal(cleaned) if cleaned else Decimal("0")
    except InvalidOperation:
        return Decimal("0")


def _safe_str(val, col_map: dict, field: str, row) -> Optional[str]:
    """Return stripped string from row if column exists, else None."""
    col = col_map.get(field)
    if col is None:
        return None
    raw = row.get(col)
    if pd.isna(raw):
        return None
    s = str(raw).strip()
    return s or None


# ── Cost-sheet parser ─────────────────────────────────────────────────────────

def parse_cost_sheet_df(
    df: pd.DataFrame,
    project: Project,
    source: str = "csv_import",
) -> tuple[list, list]:
    """
    Parse a cost-sheet DataFrame into Transaction objects.
    Returns (transactions, errors).
    """
    col_map = _resolve_columns(df)

    if "date" not in col_map or "amount" not in col_map:
        raise ValueError(
            "CSV must contain at least a date column and an amount column. "
            f"Detected columns: {list(df.columns)}"
        )

    transactions: list[Transaction] = []
    errors: list[dict] = []

    for idx, row in df.iterrows():
        try:
            amount = _parse_amount(row[col_map["amount"]])
            if amount == Decimal("0"):
                continue  # skip zero-amount rows

            raw_category = _safe_str(row, col_map, "category", row)
            description = _safe_str(row, col_map, "description", row)

            category = resolve_best(raw_category, description)
            cost_head = _get_or_create_cost_head(project, category)

            txn = Transaction(
                project_id=project.id,
                cost_head_id=cost_head.id,
                transaction_date=_parse_date(row[col_map["date"]]),
                amount_inr=amount,
                transaction_type=CATEGORY_TO_TXN_TYPE.get(category, "material_purchase"),
                description=description,
                vendor_name=_safe_str(row, col_map, "vendor", row),
                invoice_number=_safe_str(row, col_map, "invoice_no", row),
                gst_amount_inr=_parse_amount(row.get(col_map.get("gst", ""), 0)),
                tds_amount_inr=_parse_amount(row.get(col_map.get("tds", ""), 0)),
                source=source,
                raw_line_item=description or raw_category,
            )
            transactions.append(txn)

        except Exception as exc:
            errors.append({"row": int(idx) + 2, "error": str(exc)})

    return transactions, errors


# ── Entry point ───────────────────────────────────────────────────────────────

def ingest_csv(
    file_bytes: bytes,
    project_id: str,
    organisation_id: str,
) -> dict:
    """
    Parse and persist a CSV file (cost sheet or Tally export).

    Returns:
        {
            "format": "cost_sheet" | "tally",
            "imported": int,
            "skipped": int,
            "errors": [{"row": int, "error": str}, ...]
        }
    """
    project = Project.query.filter_by(
        id=project_id,
        organisation_id=organisation_id,
    ).first_or_404()

    try:
        df = pd.read_csv(io.BytesIO(file_bytes), dtype=str, skip_blank_lines=True)
    except Exception as exc:
        raise ValueError(f"Could not parse CSV file: {exc}")

    df = df.dropna(how="all")

    if is_tally_format(df):
        fmt = "tally"
        transactions, errors = parse_tally_df(df, project)
    else:
        fmt = "cost_sheet"
        transactions, errors = parse_cost_sheet_df(df, project)

    db.session.add_all(transactions)
    db.session.commit()

    return {
        "format": fmt,
        "imported": len(transactions),
        "skipped": len(errors),
        "errors": errors,
    }
