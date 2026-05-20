"""
Tally ERP Purchase/Payment voucher parser.

Handles the two real-world Tally export layouts:

  Flat layout (one complete row per purchase):
    Voucher Date | Voucher Type | Party Name | Ledger Name | Debit | Credit | Narration

  Multi-entry layout (one row per ledger line; date/party blank on continuation rows):
    15-Jan-24 | Purchase | Ultratech | Purchase - Civil | 450000 |        | Cement bags
              |          |           | CGST 9%          |  40500 |        |
              |          |           | SGST 9%          |  40500 |        |
              |          | Ultratech | Sundry Creditors |        | 531000 |

Processing pipeline
───────────────────
  1. read_tally_file()   — load CSV or Excel (.xls/.xlsx); strip company-header rows
  2. _normalise_cols()   — map Tally version variants → canonical names
  3. _forward_fill()     — fill blank date/voucher_no/party in continuation rows
  4. _is_skip_ledger()   — drop GST, TDS, TCS, bank, cash, creditor rows
  5. parse_tally_df()    — resolve category, dedup by voucher hash, build Transactions
  6. ingest_tally_file() — top-level: load → parse → persist → return summary

Idempotency
───────────
  Dedup key = SHA-1 of (project_id, voucher_date, voucher_no, ledger, debit_amount).
  Stored as invoice_number on Transaction.  Re-import skips rows whose key already
  exists in the DB.  If the export lacks a voucher number the date+amount+ledger
  hash is used instead.
"""
from __future__ import annotations

import hashlib
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import pandas as pd

from .normaliser import resolve_best


# ── Column-name variants across Tally ERP 9 / Prime / TallyPrime ─────────────

_COL_CANDIDATES: dict[str, list[str]] = {
    "date":         ["voucher date", "date", "voucher_date", "vch date", "txn date"],
    "voucher_type": ["voucher type", "vch type", "type", "voucher_type"],
    "voucher_no":   ["voucher no.", "voucher no", "vch no.", "vch no", "no.", "ref no",
                     "reference no", "voucher number", "vch number"],
    "party":        ["party name", "party's name", "name", "party", "party_name",
                     "account", "counter party"],
    "ledger":       ["ledger name", "particulars", "ledger", "account name",
                     "ledger_name", "account"],
    "debit":        ["debit amount", "dr amount", "debit", "dr", "debit_amount"],
    "credit":       ["credit amount", "cr amount", "credit", "cr", "credit_amount"],
    "narration":    ["narration", "description", "memo", "remarks", "details", "note"],
}

# Voucher types we want to ingest (Tally has 20+ types)
_INGEST_TYPES = {"purchase", "payment", "journal"}

# Ledger-name patterns that are NOT expenses — skip these rows entirely
_SKIP_LEDGER_RE = re.compile(
    r"""
    \b(
        cgst | sgst | igst | ugst |          # GST components
        input\s+cgst | input\s+sgst |
        input\s+igst | gst\s+payable |
        tds | tcs |                           # tax deducted/collected
        sundry\s+cred | sundry\s+deb |        # creditors / debtors
        accounts\s+payable | accounts\s+rec | # AP/AR
        bank | cash | petty\s+cash |          # cash/bank
        capital\s+account | retained |        # equity
        loan | overdraft |                    # financing
        opening\s+stock | closing\s+stock     # stock
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


# ── File reading ──────────────────────────────────────────────────────────────

def read_tally_file(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """
    Load a Tally export from bytes.  Handles:
      - CSV (any encoding; tries UTF-8 then Latin-1)
      - Excel .xls / .xlsx
      - Files with company-info header rows before the column-header row
        (scans the first 15 rows for the real header)

    Raises ValueError if the file cannot be parsed.
    """
    fname = (filename or "").lower()

    if fname.endswith((".xlsx", ".xls")):
        df = _read_excel(file_bytes, fname)
    elif fname.endswith(".csv"):
        df = _read_csv(file_bytes)
    else:
        # Try CSV first, then Excel
        try:
            df = _read_csv(file_bytes)
        except Exception:
            try:
                df = _read_excel(file_bytes, "unknown.xlsx")
            except Exception as exc:
                raise ValueError(f"Cannot parse file: {exc}")

    return df.dropna(how="all")


def _read_csv(file_bytes: bytes) -> pd.DataFrame:
    """
    Decode and parse a Tally CSV, tolerating company-info header rows that
    have fewer columns than the data rows (which would make pd.read_csv fail).

    Strategy: decode → find the real column-header row by scanning for known
    Tally keywords → slice the text from that row onwards → hand to pd.read_csv.
    Falls back to reading the whole file if no header row is found.
    """
    tally_keywords = {"voucher date", "date", "vch type", "voucher type",
                      "ledger name", "particulars", "debit amount", "debit"}

    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = file_bytes.decode(enc)
        except UnicodeDecodeError:
            continue

        lines = text.splitlines()
        header_idx = None
        for i, line in enumerate(lines[:20]):          # only scan first 20 rows
            lower_cells = {c.strip().lower() for c in line.split(",")}
            if lower_cells & tally_keywords:
                header_idx = i
                break

        start = header_idx if header_idx is not None else 0
        trimmed = "\n".join(lines[start:])

        try:
            df = pd.read_csv(io.BytesIO(trimmed.encode(enc)), dtype=str,
                             skip_blank_lines=True)
            return df
        except Exception:
            pass   # fall through to next encoding

    raise ValueError("Could not decode or parse CSV")


def _read_excel(file_bytes: bytes, fname: str) -> pd.DataFrame:
    engine = "xlrd" if fname.endswith(".xls") else "openpyxl"
    try:
        xf = pd.ExcelFile(io.BytesIO(file_bytes), engine=engine)
    except Exception:
        # openpyxl can also read older xlsx; try without specifying engine
        xf = pd.ExcelFile(io.BytesIO(file_bytes))

    # Try first sheet; if it looks like a cover sheet, try the second
    for sheet in xf.sheet_names[:3]:
        df = pd.read_excel(xf, sheet_name=sheet, dtype=str, header=None)
        df = _strip_company_header(df)
        if is_tally_format(df):
            return df

    # Fall back: return first sheet as-is after stripping
    df = pd.read_excel(xf, sheet_name=xf.sheet_names[0], dtype=str, header=None)
    return _strip_company_header(df)


def _strip_company_header(df: pd.DataFrame) -> pd.DataFrame:
    """
    Tally exports often prepend company name, report title, and date-range rows
    before the actual column headers.  Scan the first 15 rows for a row that
    looks like Tally column headers; if found, promote it and discard rows above.
    """
    tally_keywords = {"voucher date", "date", "vch type", "voucher type",
                      "ledger name", "particulars", "debit amount", "debit"}

    for i, row in df.head(15).iterrows():
        cell_vals = {str(v).strip().lower() for v in row if pd.notna(v)}
        if cell_vals & tally_keywords:
            # Promote this row to column headers, drop rows above
            df.columns = df.iloc[i].astype(str).str.strip()
            df = df.iloc[i + 1:].reset_index(drop=True)
            return df

    # Already has proper headers (or couldn't detect)
    return df


# ── Format detection ──────────────────────────────────────────────────────────

def is_tally_format(df: pd.DataFrame) -> bool:
    """Return True if the DataFrame looks like a Tally export."""
    cols = {c.strip().lower() for c in df.columns if pd.notna(c)}
    required = {"voucher date", "date"} & cols
    ledger   = {"ledger name", "particulars", "ledger"} & cols
    amount   = {"debit amount", "debit", "dr amount", "dr", "credit amount", "credit"} & cols
    return bool(required) and bool(ledger) and bool(amount)


# ── Column normalisation ──────────────────────────────────────────────────────

def _normalise_cols(df: pd.DataFrame) -> dict[str, str | None]:
    """
    Return {canonical_field: actual_column_name | None} by matching
    _COL_CANDIDATES against the DataFrame's lowercased columns.
    """
    lower_to_orig = {c.strip().lower(): c for c in df.columns if pd.notna(c)}
    resolved: dict[str, str | None] = {}
    for field, candidates in _COL_CANDIDATES.items():
        for cand in candidates:
            if cand in lower_to_orig:
                resolved[field] = lower_to_orig[cand]
                break
        else:
            resolved[field] = None
    return resolved


# ── Multi-entry forward-fill ──────────────────────────────────────────────────

def _forward_fill(df: pd.DataFrame, cols: dict[str, str | None]) -> pd.DataFrame:
    """
    In the multi-entry layout, continuation rows (same voucher, additional ledger
    lines) have a blank date.  Forward-fill date/voucher_no/voucher_type for all
    rows, but fill party ONLY for continuation rows — blank-date rows within the
    same voucher.  Rows that start a new voucher (non-blank date) keep their own
    party even if it is empty, so a Payment with no party doesn't inherit the
    previous Purchase's vendor.
    """
    df = df.copy()

    def _blank_na(series):
        return series.replace(r"^\s*$", pd.NA, regex=True).replace("nan", pd.NA)

    # Identify continuation rows before ffill changes anything
    date_col = cols.get("date")
    if date_col and date_col in df.columns:
        is_continuation = _blank_na(df[date_col]).isna()
    else:
        is_continuation = pd.Series(False, index=df.index)

    # Forward-fill date, voucher_type, voucher_no unconditionally (they're
    # always blank on continuation rows and always present on the first row)
    for field in ("date", "voucher_type", "voucher_no"):
        col = cols.get(field)
        if col and col in df.columns:
            df[col] = _blank_na(df[col]).ffill()

    # Forward-fill party ONLY into continuation rows
    party_col = cols.get("party")
    if party_col and party_col in df.columns:
        original = _blank_na(df[party_col])
        filled   = original.ffill()
        df[party_col] = filled.where(is_continuation, original)

    return df


# ── Skip-row detection ────────────────────────────────────────────────────────

def _is_skip_ledger(ledger: str) -> bool:
    """True for GST, TDS, bank, cash, and liability ledger lines."""
    return bool(_SKIP_LEDGER_RE.search(ledger.strip()))


# ── Amount / date parsing ─────────────────────────────────────────────────────

_DATE_FORMATS = (
    "%d-%b-%y", "%d-%b-%Y",
    "%d-%m-%Y",  "%d/%m/%Y",
    "%d-%m-%y",  "%d/%m/%y",
    "%Y-%m-%d",  "%m/%d/%Y",
)


def _parse_date(raw) -> date:
    s = str(raw).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date: {s!r}")


def _parse_amount(raw) -> Decimal:
    if pd.isna(raw) or str(raw).strip() in ("", "-", "nil"):
        return Decimal("0")
    cleaned = (
        str(raw)
        .replace(",", "").replace("₹", "").replace("Rs.", "")
        .replace("Rs", "").replace(" ", "").strip()
    )
    try:
        return Decimal(cleaned) if cleaned else Decimal("0")
    except InvalidOperation:
        return Decimal("0")


def _cell(row, col: str | None):
    if col is None:
        return None
    val = row.get(col)
    return None if (val is None or (isinstance(val, float) and pd.isna(val))) else str(val).strip()


# ── Dedup key ─────────────────────────────────────────────────────────────────

def _dedup_key(project_id, txn_date: date, voucher_no: str | None,
               ledger: str, amount: Decimal) -> str:
    """
    SHA-1 hash of key fields, stored as invoice_number.
    Ensures re-import of the same file is idempotent.
    """
    vno = voucher_no or ""
    raw = f"{project_id}|{txn_date}|{vno}|{ledger}|{amount}"
    return "tally-" + hashlib.sha1(raw.encode()).hexdigest()[:16]


# ── Voucher type → transaction type ──────────────────────────────────────────

def _txn_type(voucher_type: str, category: str) -> str:
    vt = voucher_type.lower()
    if category == "Labour":
        return "labour"
    if vt == "payment":
        return "contractor_payment"
    return "material_purchase"


# ── Main parser ───────────────────────────────────────────────────────────────

def parse_tally_df(df: pd.DataFrame, project) -> tuple[list, list, int]:
    """
    Parse a Tally export DataFrame into Transaction objects.

    Returns (transactions, errors, skipped_duplicates).
    Does NOT commit — caller must db.session.add_all(transactions) + commit().
    """
    from ..extensions import db
    from ..models.cost_head import CostHead
    from ..models.transaction import Transaction

    cols = _normalise_cols(df)
    df   = _forward_fill(df, cols)

    # Pre-load existing dedup keys for this project to avoid one-query-per-row
    existing_keys: set[str] = {
        t.invoice_number
        for t in Transaction.query.filter(
            Transaction.project_id == project.id,
            Transaction.source == "tally_export",
        )
        .with_entities(Transaction.invoice_number)
        .all()
        if t.invoice_number
    }

    # Cache cost heads to avoid repeated queries
    _head_cache: dict[str, CostHead] = {}

    def _get_or_create_head(category: str) -> CostHead:
        if category not in _head_cache:
            head = CostHead.query.filter_by(
                project_id=project.id, category=category
            ).first()
            if head is None:
                head = CostHead(
                    project_id=project.id, name=category,
                    category=category, budgeted_amount_inr=Decimal("0"),
                )
                db.session.add(head)
                db.session.flush()
            _head_cache[category] = head
        return _head_cache[category]

    transactions: list[Transaction] = []
    errors:        list[dict]       = []
    skipped_dup:   int              = 0

    for idx, row in df.iterrows():
        try:
            # ── Voucher type filter ───────────────────────────────────────────
            vtype_raw = _cell(row, cols["voucher_type"]) or ""
            if vtype_raw.lower() not in _INGEST_TYPES:
                continue

            # ── Ledger name ───────────────────────────────────────────────────
            ledger = _cell(row, cols["ledger"]) or ""
            if not ledger or _is_skip_ledger(ledger):
                continue

            # ── Amount — debit side only ──────────────────────────────────────
            debit = _parse_amount(_cell(row, cols["debit"]))
            if debit == Decimal("0"):
                continue

            # ── Date ──────────────────────────────────────────────────────────
            date_raw = _cell(row, cols["date"])
            if not date_raw:
                continue
            txn_date = _parse_date(date_raw)

            # ── Voucher number → dedup key ────────────────────────────────────
            voucher_no = _cell(row, cols["voucher_no"])
            key = _dedup_key(project.id, txn_date, voucher_no, ledger, debit)
            if key in existing_keys:
                skipped_dup += 1
                continue
            existing_keys.add(key)

            # ── Category resolution ───────────────────────────────────────────
            narration = _cell(row, cols["narration"]) or ""
            category  = resolve_best(ledger, narration)
            head      = _get_or_create_head(category)

            # ── Build Transaction ─────────────────────────────────────────────
            party = _cell(row, cols["party"])
            transactions.append(Transaction(
                project_id       = project.id,
                cost_head_id     = head.id,
                transaction_date = txn_date,
                amount_inr       = debit,
                transaction_type = _txn_type(vtype_raw, category),
                description      = narration or ledger,
                vendor_name      = party or None,
                invoice_number   = key,
                source           = "tally_export",
                raw_line_item    = f"{ledger} | {narration}".strip(" |"),
            ))

        except Exception as exc:
            errors.append({"row": int(idx) + 2, "error": str(exc)})

    return transactions, errors, skipped_dup


# ── Top-level entry point ─────────────────────────────────────────────────────

def ingest_tally_file(file_bytes: bytes, filename: str, project) -> dict:
    """
    Load, parse, and persist a Tally export.

    Returns:
      {
        "format":            "tally",
        "filename":          str,
        "imported":          int,
        "skipped_duplicate": int,
        "errors":            [{row, error}, ...]
      }

    Raises ValueError if the file is not recognisable as a Tally export.
    Rolls back the session on unexpected errors.
    """
    from ..extensions import db

    df = read_tally_file(file_bytes, filename)

    if not is_tally_format(df):
        raise ValueError(
            "File does not appear to be a Tally export. "
            "Expected columns: Voucher Date, Voucher Type, Ledger Name, Debit Amount."
        )

    try:
        transactions, errors, skipped = parse_tally_df(df, project)
        db.session.add_all(transactions)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return {
        "format":            "tally",
        "filename":          filename,
        "imported":          len(transactions),
        "skipped_duplicate": skipped,
        "errors":            errors,
    }
