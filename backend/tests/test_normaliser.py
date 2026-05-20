"""
Tests for the canonical cost-head normaliser.

Covers all 7 categories across:
  - Standard names (exact + case variants)
  - Paper-record abbreviations (CC, RCC, E/W, P/W, P&M, etc.)
  - Tally ledger prefixes ("Purchase - Civil", "Labour Expenses")
  - Free-text invoice descriptions ("OPC 53 Cement 200 bags")
  - Hindi transliterations (Majdoor, Bijli, Rang, Neev)
  - Typos via fuzzy matching
  - Edge cases (empty, whitespace, numbers-only)
"""
from unittest.mock import patch

import pytest

from app.services.normaliser import (
    CANONICAL,
    KEYWORD_MAP,
    resolve_best,
    resolve_category,
    _normalise,
    _exact,
    _substring,
    _token_match,
    _fuzzy,
)


# ── Invariants ────────────────────────────────────────────────────────────────

def test_all_keyword_map_values_are_canonical():
    for key, val in KEYWORD_MAP.items():
        assert val in CANONICAL, f"KEYWORD_MAP[{key!r}] = {val!r} is not a canonical category"


def test_canonical_list_unchanged():
    assert set(CANONICAL) == {
        "Civil Structure", "MEP", "Finishing",
        "External Development", "Labour", "Equipment", "Misc",
    }


# ── Preprocessing ─────────────────────────────────────────────────────────────

class TestNormalise:
    def test_lowercase(self):
        assert _normalise("Civil Structure") == "civil structure"

    def test_strips_tally_purchase_prefix(self):
        assert _normalise("Purchase - Civil") == "civil"

    def test_strips_tally_payment_prefix(self):
        assert _normalise("Payment - Labour") == "labour"

    def test_strips_expenses_prefix(self):
        assert _normalise("Expenses: MEP") == "mep"

    def test_collapses_ampersand(self):
        assert _normalise("Doors & Windows") == "doors  windows".replace("  ", " ")

    def test_collapses_whitespace(self):
        assert _normalise("  civil   works  ") == "civil works"


# ── Civil Structure ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Civil Structure", "civil", "Civil Work", "Civil Works",
    "RCC", "R.C.C.", "rcc work", "PCC", "P.C.C.", "pcc work",
    "Concrete", "Ready Mix Concrete", "RMC",
    "Cement", "OPC Cement", "Steel", "TMT Bars", "TMT Steel", "MS Steel",
    "Sand", "River Sand", "M-Sand", "Aggregate", "Grit",
    "Foundation", "Neev", "Footing",
    "Brickwork", "Masonry", "AAC Blocks", "Fly Ash Bricks",
    "Shuttering", "Formwork", "Centering",
    "Excavation", "Backfilling", "Waterproofing", "DPC",
    "Purchase - Civil", "Purchase - Structure",
])
def test_civil_structure(text):
    assert resolve_category(text) == "Civil Structure", f"Failed for {text!r}"


# ── MEP ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "MEP", "M&E", "Electrical", "Electrical Work", "Electricals",
    "E/W", "Bijli", "Bijli Kaam",
    "Wiring", "Conduit", "Conduit & Wiring", "Switchgear", "Earthing",
    "Solar Panel", "DG Set", "Generator",
    "Plumbing", "Plumbing Work", "P/W", "Paani Kaam",
    "CPVC", "Pipes & Fittings", "Sanitary Fittings", "CP Fittings",
    "HVAC", "Air Conditioning", "Ducting",
    "Fire Fighting", "Fire Safety", "Sprinkler",
    "Lift", "Elevator",
    "Purchase - MEP",
])
def test_mep(text):
    assert resolve_category(text) == "MEP", f"Failed for {text!r}"


# ── Finishing ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Finishing", "Finishing Work", "Interior", "Interiors",
    "Tiles", "Floor Tiles", "Vitrified Tiles", "Tiling",
    "Flooring", "Granite", "Marble", "Kota Stone",
    "Painting", "Paint", "Rang", "Rangai",
    "Primer", "Distemper", "Emulsion", "Texture Paint",
    "Plastering", "Plaster", "POP", "Gypsum",
    "False Ceiling", "F/C", "Gypsum Ceiling",
    "Doors", "Windows", "Doors & Windows", "D&W",
    "Aluminium", "Aluminium Work", "Glass", "Glazing",
    "Woodwork", "Carpentry", "Modular Kitchen", "Wardrobes",
    "MS Railing", "SS Railing",
    "Purchase - Finishing", "Purchase - Interior",
])
def test_finishing(text):
    assert resolve_category(text) == "Finishing", f"Failed for {text!r}"


# ── External Development ──────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "External Development", "External", "External Works", "Ext Dev",
    "Landscaping", "Lawn", "Garden", "Horticulture",
    "Compound Wall", "Boundary Wall", "C/W", "B/W", "Fencing",
    "Gate", "Main Gate",
    "Road", "Road Works", "Internal Road", "Paving", "Interlocking",
    "Parking", "Footpath",
    "Drainage", "Naali", "STP", "Sewage Treatment",
    "Rainwater Harvesting", "RWH", "Overhead Tank", "OHT",
    "Street Light", "Street Lighting",
    "Swimming Pool", "Clubhouse", "Amenities", "Common Area",
    "Purchase - External",
])
def test_external_development(text):
    assert resolve_category(text) == "External Development", f"Failed for {text!r}"


# ── Labour ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Labour", "Labor", "Labour Charges", "Labour Expenses",
    "Wages", "Site Wages", "Daily Wages", "D/W",
    "Manpower", "Manpower Supply",
    "Contract Labour", "Skilled Labour", "Unskilled Labour",
    "Majdoor", "Mazdar",
    "Labour Expenses",   # Tally ledger name
    "Wages Payable",     # Tally ledger name
])
def test_labour(text):
    assert resolve_category(text) == "Labour", f"Failed for {text!r}"


# ── Equipment ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Equipment", "Equipment Hire", "Plant", "Plant & Machinery",
    "P&M", "Machinery", "Machinery Hire", "M/C",
    "JCB", "JCB Charges", "Crane", "Crane Hire",
    "Excavator", "Loader", "Dumper", "Tipper",
    "Scaffolding", "Scaffold", "Scaffolding Charges",
    "Transit Mixer", "Concrete Pump",
    "Vibrator", "Compactor", "Mixer Machine",
    "Tools", "Tools & Tackles",
    "Hire Charges",
])
def test_equipment(text):
    assert resolve_category(text) == "Equipment", f"Failed for {text!r}"


# ── Misc ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Misc", "Miscellaneous", "Miscellaneous Expenses",
    "General", "General Expenses",
    "Petty Cash", "P/C",
    "Site Expenses", "Office Expenses",
    "Insurance", "Bank Charges", "Legal Fees",
    "Professional Fees", "Architect Fees",
    "NOC", "Liaison",
    "Soil Testing", "Quality Testing",
    "Stamp Duty", "Other", "Others",
    "Direct Expenses", "Indirect Expenses",
])
def test_misc(text):
    assert resolve_category(text) == "Misc", f"Failed for {text!r}"


# ── Token matching (free-text descriptions) ───────────────────────────────────

class TestTokenMatch:
    def test_cement_in_description(self):
        assert resolve_category("OPC 53 Cement 200 bags Ultratech") == "Civil Structure"

    def test_steel_in_description(self):
        assert resolve_category("TMT Steel Bars Fe500 12mm Shyam") == "Civil Structure"

    def test_labour_in_description(self):
        assert resolve_category("Site Labour wages January 2024") == "Labour"

    def test_electrical_in_description(self):
        assert resolve_category("Electrical conduit wiring Phase 1") == "MEP"

    def test_tiles_in_description(self):
        assert resolve_category("Floor tiles living room Kajaria") == "Finishing"

    def test_jcb_in_description(self):
        assert resolve_category("JCB excavation and levelling charges") == "Equipment"

    def test_landscaping_in_description(self):
        assert resolve_category("Landscaping phase 1 garden area") == "External Development"

    def test_petty_cash_in_description(self):
        assert resolve_category("Site office petty cash expenses") == "Misc"

    def test_scaffolding_in_description(self):
        assert resolve_category("Scaffolding hire charges March") == "Equipment"

    def test_painting_in_description(self):
        assert resolve_category("Painting materials primer emulsion Asian Paints") == "Finishing"


# ── Tally ledger names ────────────────────────────────────────────────────────

class TestTallyLedgerNames:
    @pytest.mark.parametrize("ledger,expected", [
        ("Purchase - Civil", "Civil Structure"),
        ("Purchase - MEP", "MEP"),
        ("Purchase - Finishing", "Finishing"),
        ("Purchase - External", "External Development"),
        ("Labour Expenses", "Labour"),
        ("Plant & Machinery", "Equipment"),
        ("Miscellaneous Expenses", "Misc"),
        ("Wages Payable", "Labour"),
        ("Civil Works", "Civil Structure"),
        ("Electrical Works", "MEP"),
        ("Plumbing Works", "MEP"),
    ])
    def test_tally_ledger(self, ledger, expected):
        assert resolve_category(ledger) == expected


# ── Fuzzy matching ────────────────────────────────────────────────────────────

class TestFuzzyMatch:
    @pytest.mark.parametrize("typo,expected", [
        ("labuur", "Labour"),
        ("laabour", "Labour"),
        ("concrte", "Civil Structure"),
        ("electrcal", "MEP"),
        ("plumbing", "MEP"),       # exact, not fuzzy — but should still pass
        ("scafolding", "Equipment"),
        ("landscapping", "External Development"),
        ("finsihing", "Finishing"),
    ])
    def test_typos(self, typo, expected):
        assert resolve_category(typo) == expected, f"Fuzzy failed for {typo!r}"


# ── resolve_best (two-field resolution) ──────────────────────────────────────

class TestResolveBest:
    def test_category_takes_precedence(self):
        assert resolve_best("Civil Structure", "something else") == "Civil Structure"

    def test_falls_back_to_description_when_category_is_misc(self):
        assert resolve_best("Misc", "TMT Steel purchase") == "Civil Structure"

    def test_falls_back_to_description_when_category_empty(self):
        assert resolve_best(None, "Electrical wiring work") == "MEP"

    def test_both_empty_returns_misc(self):
        assert resolve_best(None, None) == "Misc"

    def test_category_wins_if_not_misc(self):
        # Even if description says "cement", category "Labour" wins
        assert resolve_best("Labour", "Cement supply") == "Labour"


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_string(self):
        assert resolve_category("") == "Misc"

    def test_whitespace_only(self):
        assert resolve_category("   ") == "Misc"

    def test_numbers_only(self):
        assert resolve_category("12345") == "Misc"

    def test_case_insensitive(self):
        assert resolve_category("CIVIL STRUCTURE") == "Civil Structure"
        assert resolve_category("civil structure") == "Civil Structure"
        assert resolve_category("Civil Structure") == "Civil Structure"

    def test_extra_whitespace(self):
        assert resolve_category("  civil   works  ") == "Civil Structure"

    def test_ai_not_called_for_known_term(self):
        with patch("app.services.normaliser._claude") as mock_claude:
            resolve_category.cache_clear()
            resolve_category("RCC")
            mock_claude.assert_not_called()

    def test_result_is_always_canonical(self):
        samples = [
            "cement bags supply", "bijli kaam phase 2",
            "rang work bathroom", "jcb hire april",
            "majdoor march", "stp construction",
            "purchase - finishing interior",
        ]
        for text in samples:
            result = resolve_category(text)
            assert result in CANONICAL, f"Non-canonical result {result!r} for {text!r}"
