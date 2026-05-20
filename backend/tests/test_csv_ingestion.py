"""
Unit tests for CSV ingestion.
Tests cover pure utility functions (no DB, no AI API calls required).
"""
import io
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from app.services.csv_ingestion import (
    _parse_amount,
    _parse_date,
    _resolve_columns,
)
from app.services.normaliser import CANONICAL, KEYWORD_MAP, resolve_category
from app.services.tally_parser import is_tally_format

FIXTURES = Path(__file__).parent / "fixtures"


# ── Date parsing ─────────────────────────────────────────────────────────────

class TestParseDate:
    def test_iso(self):
        assert _parse_date("2024-03-15") == date(2024, 3, 15)

    def test_indian_dash(self):
        assert _parse_date("15-03-2024") == date(2024, 3, 15)

    def test_indian_slash(self):
        assert _parse_date("15/03/2024") == date(2024, 3, 15)

    def test_tally_month_abbr(self):
        assert _parse_date("15-Mar-24") == date(2024, 3, 15)

    def test_tally_month_abbr_full_year(self):
        assert _parse_date("15-Mar-2024") == date(2024, 3, 15)

    def test_two_digit_year(self):
        assert _parse_date("15-03-24") == date(2024, 3, 15)

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            _parse_date("not-a-date")


# ── Amount parsing ────────────────────────────────────────────────────────────

class TestParseAmount:
    def test_plain_integer(self):
        assert _parse_amount("180000") == Decimal("180000")

    def test_indian_commas(self):
        assert _parse_amount("1,80,000") == Decimal("180000")

    def test_rupee_symbol(self):
        assert _parse_amount("₹95,000") == Decimal("95000")

    def test_rs_prefix(self):
        assert _parse_amount("Rs. 62,000") == Decimal("62000")

    def test_decimal(self):
        assert _parse_amount("95000.50") == Decimal("95000.50")

    def test_empty_string(self):
        assert _parse_amount("") == Decimal("0")

    def test_dash(self):
        assert _parse_amount("-") == Decimal("0")

    def test_nan(self):
        import math
        assert _parse_amount(float("nan")) == Decimal("0")

    def test_float_input(self):
        assert _parse_amount(180000.0) == Decimal("180000.0")


# ── Column detection ─────────────────────────────────────────────────────────

class TestResolveColumns:
    def test_standard_columns(self):
        df = pd.DataFrame(columns=["date", "description", "category", "vendor", "amount", "invoice_no", "gst", "tds"])
        cols = _resolve_columns(df)
        assert cols["date"] == "date"
        assert cols["amount"] == "amount"
        assert cols["description"] == "description"

    def test_tally_style_columns(self):
        df = pd.DataFrame(columns=["Voucher Date", "Narration", "Ledger Name", "Party Name", "Debit Amount"])
        cols = _resolve_columns(df)
        assert cols.get("date") == "Voucher Date"
        assert cols.get("amount") == "Debit Amount"

    def test_missing_required_columns(self):
        df = pd.DataFrame(columns=["description", "vendor"])
        cols = _resolve_columns(df)
        assert "date" not in cols
        assert "amount" not in cols


# ── Tally format detection ────────────────────────────────────────────────────

class TestTallyFormatDetection:
    def test_detects_tally(self):
        df = pd.read_csv(FIXTURES / "sample_tally_export.csv", dtype=str)
        assert is_tally_format(df) is True

    def test_rejects_cost_sheet(self):
        df = pd.read_csv(FIXTURES / "sample_cost_sheet.csv", dtype=str)
        assert is_tally_format(df) is False


# ── Fixture CSV smoke tests ───────────────────────────────────────────────────

class TestFixtureParsing:
    """Smoke tests: parse fixtures without a DB — just verify no exceptions."""

    def test_cost_sheet_parses_without_error(self):
        df = pd.read_csv(FIXTURES / "sample_cost_sheet.csv", dtype=str)
        col_map_result = _resolve_columns(df)
        assert "date" in col_map_result
        assert "amount" in col_map_result
        # Verify all dates and amounts parse cleanly
        for _, row in df.iterrows():
            _parse_date(row[col_map_result["date"]])
            _parse_amount(row[col_map_result["amount"]])

    def test_tally_export_parses_without_error(self):
        df = pd.read_csv(FIXTURES / "sample_tally_export.csv", dtype=str)
        assert is_tally_format(df)
        # Verify all dates and debit amounts parse cleanly
        for _, row in df.iterrows():
            _parse_date(row["Voucher Date"])
            _parse_amount(row.get("Debit Amount", "0"))

    def test_cost_sheet_row_count(self):
        df = pd.read_csv(FIXTURES / "sample_cost_sheet.csv")
        assert len(df) == 22

    def test_tally_row_count(self):
        df = pd.read_csv(FIXTURES / "sample_tally_export.csv")
        assert len(df) == 22
