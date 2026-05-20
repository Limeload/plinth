"""
Loads contractor master data and payment transactions from CSVs.

Contractor master CSV columns:
  name, work_package, contract_amount_inr, payment_terms, retention_percentage

Payment CSV columns:
  contractor, payment_date, amount_inr, milestone_name (optional),
  invoice_number, description, gst_amount_inr, tds_amount_inr
"""
from datetime import date, datetime
from decimal import Decimal

import pandas as pd

from ..extensions import db
from ..models.contractor import Contractor
from ..models.cost_head import CostHead
from ..models.milestone import Milestone
from ..models.transaction import Transaction

_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d-%b-%y")

_WORK_PACKAGE_TO_CATEGORY = {
    "civil structure":      "Civil Structure",
    "mep":                  "MEP",
    "finishing":            "Finishing",
    "external development": "External Development",
    "labour":               "Labour",
    "equipment":            "Equipment",
    "misc":                 "Misc",
}


def _parse_date(raw) -> date:
    s = str(raw).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date: {s!r}")


def _parse_decimal(raw, default="0") -> Decimal:
    s = str(raw).strip() if raw and not (isinstance(raw, float) and pd.isna(raw)) else default
    if not s or s in ("-", "n/a"):
        return Decimal(default)
    return Decimal(s.replace(",", ""))


def load_contractors_from_csv(csv_path, project) -> tuple[int, list[dict]]:
    """
    Upsert contractor master rows. Upsert key: (project_id, name).
    Returns (loaded_count, errors).
    """
    df = pd.read_csv(csv_path, dtype=str, skip_blank_lines=True).dropna(how="all")
    loaded = 0
    errors: list[dict] = []

    for idx, row in df.iterrows():
        try:
            name = str(row["name"]).strip()
            if not name:
                continue

            existing = Contractor.query.filter_by(
                project_id=project.id, name=name
            ).first()

            if existing:
                existing.work_package      = str(row.get("work_package", "")).strip() or None
                existing.contract_amount_inr = _parse_decimal(row.get("contract_amount_inr"))
                existing.payment_terms     = str(row.get("payment_terms", "")).strip() or None
                existing.retention_percentage = _parse_decimal(row.get("retention_percentage", "0"))
            else:
                c = Contractor(
                    project_id=project.id,
                    name=name,
                    work_package=str(row.get("work_package", "")).strip() or None,
                    contract_amount_inr=_parse_decimal(row.get("contract_amount_inr")),
                    payment_terms=str(row.get("payment_terms", "")).strip() or None,
                    retention_percentage=_parse_decimal(row.get("retention_percentage", "0")),
                )
                db.session.add(c)

            loaded += 1

        except Exception as exc:
            errors.append({"row": int(idx) + 2, "error": str(exc)})

    db.session.flush()
    return loaded, errors


def load_contractor_payments_from_csv(csv_path, project) -> tuple[int, list[dict]]:
    """
    Create contractor_payment Transaction rows.

    Each row is matched to:
      - contractor by name (must already exist in DB for this project)
      - cost_head by contractor's work_package → canonical category
      - milestone by name (optional, nullable)

    Idempotent by invoice_number: skips if a transaction with that
    invoice_number already exists for this project.
    Returns (loaded_count, errors).
    """
    df = pd.read_csv(csv_path, dtype=str, skip_blank_lines=True).dropna(how="all")

    # Pre-build lookup caches to avoid N+1 queries
    contractors: dict[str, Contractor] = {
        c.name: c
        for c in Contractor.query.filter_by(project_id=project.id).all()
    }
    cost_heads: dict[str, CostHead] = {
        ch.category: ch
        for ch in CostHead.query.filter_by(project_id=project.id).all()
    }
    milestones: dict[str, Milestone] = {
        m.name: m
        for m in Milestone.query.filter_by(project_id=project.id).all()
    }
    existing_invoices: set[str] = {
        t.invoice_number
        for t in Transaction.query.filter_by(project_id=project.id).all()
        if t.invoice_number
    }

    loaded = 0
    errors: list[dict] = []

    for idx, row in df.iterrows():
        try:
            invoice_no = str(row.get("invoice_number", "")).strip() or None
            if invoice_no and invoice_no in existing_invoices:
                continue  # idempotent skip

            contractor_name = str(row["contractor"]).strip()
            contractor = contractors.get(contractor_name)
            if contractor is None:
                raise ValueError(f"Contractor not found: {contractor_name!r}")

            # Resolve cost head via work_package
            category = _WORK_PACKAGE_TO_CATEGORY.get(
                (contractor.work_package or "").lower()
            )
            if category is None:
                raise ValueError(f"Unknown work_package: {contractor.work_package!r}")

            cost_head = cost_heads.get(category)
            if cost_head is None:
                raise ValueError(f"CostHead for category {category!r} not found in project")

            # Optional milestone link
            milestone_name = str(row.get("milestone_name", "")).strip() or None
            milestone = milestones.get(milestone_name) if milestone_name else None

            # Derive transaction_type from work_package
            if (contractor.work_package or "").lower() == "labour":
                txn_type = "labour"
            elif (contractor.work_package or "").lower() == "equipment":
                txn_type = "contractor_payment"
            else:
                txn_type = "contractor_payment"

            txn = Transaction(
                project_id=project.id,
                cost_head_id=cost_head.id,
                contractor_id=contractor.id,
                milestone_id=milestone.id if milestone else None,
                transaction_date=_parse_date(row["payment_date"]),
                amount_inr=_parse_decimal(row["amount_inr"]),
                transaction_type=txn_type,
                description=str(row.get("description", "")).strip() or None,
                vendor_name=contractor_name,
                invoice_number=invoice_no,
                gst_amount_inr=_parse_decimal(row.get("gst_amount_inr", "0")),
                tds_amount_inr=_parse_decimal(row.get("tds_amount_inr", "0")),
                source="seed",
                raw_line_item=str(row.get("description", "")).strip() or contractor_name,
            )
            db.session.add(txn)
            if invoice_no:
                existing_invoices.add(invoice_no)
            loaded += 1

        except Exception as exc:
            errors.append({"row": int(idx) + 2, "error": str(exc)})

    db.session.flush()
    return loaded, errors
