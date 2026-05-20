"""
Seed script — creates the demo organisation, projects, cost heads,
and milestones used in the YC demo flow.

Run from backend/:
    flask --app app shell < seed.py
  OR
    python seed.py
"""
import os
import sys
from decimal import Decimal
from pathlib import Path

# Allow running as `python seed.py` from backend/
sys.path.insert(0, str(Path(__file__).parent))

from app import create_app
from app.extensions import db
from app.models.disbursement import Disbursement
from app.models.project import Organisation, Project
from app.models.cost_head import CostHead
from app.services.milestone_loader import load_milestones_from_csv
from app.services.contractor_loader import (
    load_contractors_from_csv,
    load_contractor_payments_from_csv,
)

SEED_DATA = Path(__file__).parent / "seed" / "data"

# ── Demo organisation ─────────────────────────────────────────────────────────

ORG_NAME = "Woods Developers Pvt. Ltd."
ORG_ID   = "00000000-0000-0000-0000-000000000001"

# ── Projects ──────────────────────────────────────────────────────────────────

PROJECTS = [
    {
        "id":                   "00000000-0000-0000-0000-000000000010",
        "name":                 "Woods Estate Phase 4 & 5",
        "total_budget_inr":     Decimal("85_000_000"),   # ₹8.5 Cr
        "sanctioned_loan_inr":  Decimal("55_250_000"),   # 65 % of budget — SBI
        "bank_name":            "State Bank of India",
        "start_date":           "2023-03-01",
        "end_date":             "2026-06-30",
        "milestone_csv":              SEED_DATA / "milestones_woods_estate.csv",
        "contractors_csv":            SEED_DATA / "contractors_woods_estate.csv",
        "contractor_payments_csv":    SEED_DATA / "contractor_payments_woods_estate.csv",
        # Bank disbursements — draw requests approved and released by SBI
        "disbursements": [
            {"date": "2023-06-15", "amount": Decimal("10_000_000"), "ref": "SBI/WE/DR01"},
            {"date": "2023-12-20", "amount": Decimal("10_000_000"), "ref": "SBI/WE/DR02"},
            {"date": "2024-04-30", "amount": Decimal("10_000_000"), "ref": "SBI/WE/DR03"},
            {"date": "2024-10-15", "amount": Decimal( "8_500_000"), "ref": "SBI/WE/DR04"},
            {"date": "2025-04-30", "amount": Decimal( "8_000_000"), "ref": "SBI/WE/DR05"},
            {"date": "2025-11-28", "amount": Decimal( "7_000_000"), "ref": "SBI/WE/DR06"},
        ],
    },
    {
        "id":                   "00000000-0000-0000-0000-000000000020",
        "name":                 "Woods Ville",
        "total_budget_inr":     Decimal("62_000_000"),   # ₹6.2 Cr
        "sanctioned_loan_inr":  Decimal("40_300_000"),   # 65 % of budget — HDFC
        "bank_name":            "HDFC Bank",
        "start_date":           "2023-09-01",
        "end_date":             "2026-09-30",
        "milestone_csv":              SEED_DATA / "milestones_woods_ville.csv",
        "contractors_csv":            SEED_DATA / "contractors_woods_ville.csv",
        "contractor_payments_csv":    SEED_DATA / "contractor_payments_woods_ville.csv",
        "disbursements": [
            {"date": "2024-02-28", "amount": Decimal("8_000_000"), "ref": "HDFC/WV/DR01"},
            {"date": "2024-08-31", "amount": Decimal("8_000_000"), "ref": "HDFC/WV/DR02"},
            {"date": "2025-02-28", "amount": Decimal("7_000_000"), "ref": "HDFC/WV/DR03"},
            {"date": "2025-08-31", "amount": Decimal("6_000_000"), "ref": "HDFC/WV/DR04"},
        ],
    },
]

# ── Cost heads with budgets ───────────────────────────────────────────────────

COST_HEADS: dict[str, list[dict]] = {
    "00000000-0000-0000-0000-000000000010": [
        {"category": "Civil Structure",      "name": "Civil Structure",      "budget": Decimal("32_000_000")},
        {"category": "MEP",                  "name": "MEP",                  "budget": Decimal("14_000_000")},
        {"category": "Finishing",            "name": "Finishing",            "budget": Decimal("18_000_000")},
        {"category": "External Development", "name": "External Development", "budget": Decimal("6_000_000")},
        {"category": "Labour",               "name": "Labour",               "budget": Decimal("8_000_000")},
        {"category": "Equipment",            "name": "Equipment",            "budget": Decimal("4_500_000")},
        {"category": "Misc",                 "name": "Misc",                 "budget": Decimal("2_500_000")},
    ],
    "00000000-0000-0000-0000-000000000020": [
        {"category": "Civil Structure",      "name": "Civil Structure",      "budget": Decimal("22_000_000")},
        {"category": "MEP",                  "name": "MEP",                  "budget": Decimal("10_000_000")},
        {"category": "Finishing",            "name": "Finishing",            "budget": Decimal("13_000_000")},
        {"category": "External Development", "name": "External Development", "budget": Decimal("5_000_000")},
        {"category": "Labour",               "name": "Labour",               "budget": Decimal("6_500_000")},
        {"category": "Equipment",            "name": "Equipment",            "budget": Decimal("3_500_000")},
        {"category": "Misc",                 "name": "Misc",                 "budget": Decimal("2_000_000")},
    ],
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_or_create_org() -> Organisation:
    org = Organisation.query.filter_by(id=ORG_ID).first()
    if org:
        print(f"  Org exists: {org.name}")
        return org
    org = Organisation(id=ORG_ID, name=ORG_NAME)
    db.session.add(org)
    db.session.flush()
    print(f"  Created org: {org.name}")
    return org


def _get_or_create_project(org: Organisation, spec: dict) -> Project:
    from datetime import datetime

    proj = Project.query.filter_by(id=spec["id"]).first()
    if proj:
        # Update loan fields in case they changed
        proj.sanctioned_loan_inr = spec.get("sanctioned_loan_inr")
        proj.bank_name           = spec.get("bank_name")
        print(f"  Project exists: {proj.name}")
        return proj

    proj = Project(
        id=spec["id"],
        organisation_id=org.id,
        name=spec["name"],
        total_budget_inr=spec["total_budget_inr"],
        sanctioned_loan_inr=spec.get("sanctioned_loan_inr"),
        bank_name=spec.get("bank_name"),
        start_date=datetime.strptime(spec["start_date"], "%Y-%m-%d").date(),
        expected_completion_date=datetime.strptime(spec["end_date"], "%Y-%m-%d").date(),
    )
    db.session.add(proj)
    db.session.flush()
    print(f"  Created project: {proj.name}")
    return proj


def _seed_cost_heads(project: Project) -> None:
    heads = COST_HEADS.get(str(project.id), [])
    for spec in heads:
        existing = CostHead.query.filter_by(
            project_id=project.id, category=spec["category"]
        ).first()
        if existing:
            existing.budgeted_amount_inr = spec["budget"]
        else:
            db.session.add(CostHead(
                project_id=project.id,
                name=spec["name"],
                category=spec["category"],
                budgeted_amount_inr=spec["budget"],
            ))
    db.session.flush()
    print(f"    Cost heads seeded: {len(heads)}")


def _seed_milestones(project: Project, csv_path: Path) -> None:
    if not csv_path.exists():
        print(f"    WARNING: milestone CSV not found: {csv_path}")
        return
    loaded, errors = load_milestones_from_csv(csv_path, project)
    print(f"    Milestones loaded: {loaded}  errors: {len(errors)}")
    for e in errors:
        print(f"      row {e['row']}: {e['error']}")


def _seed_contractors(project: Project, csv_path: Path) -> None:
    if not csv_path.exists():
        print(f"    WARNING: contractors CSV not found: {csv_path}")
        return
    loaded, errors = load_contractors_from_csv(csv_path, project)
    print(f"    Contractors loaded: {loaded}  errors: {len(errors)}")
    for e in errors:
        print(f"      row {e['row']}: {e['error']}")


def _seed_disbursements(project: Project, specs: list[dict]) -> None:
    from datetime import datetime

    existing_refs = {
        d.bank_reference
        for d in Disbursement.query.filter_by(project_id=project.id).all()
        if d.bank_reference
    }
    added = 0
    cum = sum(
        d.amount_inr for d in Disbursement.query.filter_by(project_id=project.id)
        .order_by(Disbursement.disbursement_date).all()
    )
    for spec in specs:
        if spec["ref"] in existing_refs:
            continue
        cum += spec["amount"]
        db.session.add(Disbursement(
            project_id=project.id,
            disbursement_date=datetime.strptime(spec["date"], "%Y-%m-%d").date(),
            amount_inr=spec["amount"],
            cumulative_drawn_inr=cum,
            bank_reference=spec["ref"],
        ))
        added += 1
    db.session.flush()
    print(f"    Disbursements seeded: {added} new  (total {len(specs)})")


def _seed_contractor_payments(project: Project, csv_path: Path) -> None:
    if not csv_path.exists():
        print(f"    WARNING: contractor payments CSV not found: {csv_path}")
        return
    loaded, errors = load_contractor_payments_from_csv(csv_path, project)
    print(f"    Contractor payments loaded: {loaded}  errors: {len(errors)}")
    for e in errors:
        print(f"      row {e['row']}: {e['error']}")


# ── Main ──────────────────────────────────────────────────────────────────────

def seed():
    app = create_app()
    with app.app_context():
        print("\n=== Plinth seed ===\n")

        org = _get_or_create_org()

        for spec in PROJECTS:
            print(f"\n[{spec['name']}]")
            proj = _get_or_create_project(org, spec)
            _seed_cost_heads(proj)
            _seed_milestones(proj, spec["milestone_csv"])
            _seed_contractors(proj, spec["contractors_csv"])
            _seed_contractor_payments(proj, spec["contractor_payments_csv"])
            _seed_disbursements(proj, spec.get("disbursements", []))

        db.session.commit()
        print("\n=== Done ===\n")


if __name__ == "__main__":
    seed()
