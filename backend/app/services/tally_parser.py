"""
Parses Tally ERP purchase/payment voucher CSV exports into Transaction records.

Expected Tally CSV columns:
  Voucher Date, Voucher Type, Party Name, Ledger Name, Debit Amount, Credit Amount, Narration
"""
from datetime import date, datetime
from decimal import Decimal

import pandas as pd

from .normaliser import resolve_category


def is_tally_format(df: pd.DataFrame) -> bool:
    required = {"voucher date", "voucher type", "ledger name"}
    cols = {c.strip().lower() for c in df.columns}
    return required.issubset(cols)


def _resolve_tally_category(ledger_name: str, narration: str) -> str:
    result = resolve_category(ledger_name)
    if result != "Misc":
        return result
    # Narration often has richer context than the ledger name
    if narration.strip():
        return resolve_category(narration)
    return "Misc"


def _parse_date(raw: str) -> date:
    raw = str(raw).strip()
    for fmt in ("%d-%b-%y", "%d-%b-%Y", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date: {raw!r}")


def _parse_amount(raw) -> Decimal:
    if pd.isna(raw) or str(raw).strip() in ("", "-"):
        return Decimal("0")
    cleaned = str(raw).replace(",", "").replace("₹", "").replace(" ", "").strip()
    try:
        return Decimal(cleaned) if cleaned else Decimal("0")
    except Exception:
        return Decimal("0")


def _voucher_type_to_txn_type(voucher_type: str, category: str) -> str:
    vt = voucher_type.strip().lower()
    if vt == "payment" and category == "Labour":
        return "labour"
    if vt in ("purchase", "payment"):
        return "contractor_payment" if category in ("Labour", "Equipment") else "material_purchase"
    return "material_purchase"


def parse_tally_df(df: pd.DataFrame, project) -> tuple[list, list]:
    """
    Parse a Tally export DataFrame into a list of Transaction objects.
    Returns (transactions, errors) where errors is a list of {row, error} dicts.
    """
    from ..models.transaction import Transaction
    from ..models.cost_head import CostHead
    from ..extensions import db

    # Normalise column names
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    transactions = []
    errors = []

    for idx, row in df.iterrows():
        try:
            voucher_type = str(row.get("voucher_type", "")).strip()
            if voucher_type.lower() not in ("purchase", "payment", "journal"):
                continue  # skip receipt, contra, etc.

            ledger_name = str(row.get("ledger_name", "")).strip()
            narration = str(row.get("narration", "")).strip()
            party_name = str(row.get("party_name", "")).strip()

            # Use debit amount; skip credit-only rows (those are bank receipts)
            debit = _parse_amount(row.get("debit_amount"))
            if debit == Decimal("0"):
                continue

            category = _resolve_tally_category(ledger_name, narration)

            # Find or create cost head
            cost_head = CostHead.query.filter_by(
                project_id=project.id, category=category
            ).first()
            if cost_head is None:
                cost_head = CostHead(
                    project_id=project.id,
                    name=category,
                    category=category,
                    budgeted_amount_inr=Decimal("0"),
                )
                db.session.add(cost_head)
                db.session.flush()

            txn = Transaction(
                project_id=project.id,
                cost_head_id=cost_head.id,
                transaction_date=_parse_date(row["voucher_date"]),
                amount_inr=debit,
                transaction_type=_voucher_type_to_txn_type(voucher_type, category),
                description=narration or ledger_name,
                vendor_name=party_name or None,
                source="tally_export",
                raw_line_item=narration or ledger_name,
            )
            transactions.append(txn)

        except Exception as exc:
            errors.append({"row": int(idx) + 2, "error": str(exc)})

    return transactions, errors
