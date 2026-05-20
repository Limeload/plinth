"""
Loads milestone CSVs into the milestones table for a given project.

CSV format (see seed/data/milestones_*.csv):
  phase, name, planned_date, actual_date, completion_percentage, notes
"""
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

from ..extensions import db
from ..models.milestone import Milestone

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%d-%b-%Y",
    "%d-%b-%y",
)


def _str_or_none(raw) -> str | None:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    s = str(raw).strip()
    return s or None


def _parse_date(raw) -> date | None:
    if not raw or (isinstance(raw, float) and pd.isna(raw)):
        return None
    s = str(raw).strip()
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date: {s!r}")


def load_milestones_from_csv(csv_path: Path, project) -> tuple[int, list[dict]]:
    """
    Parse a milestone CSV and upsert rows into the milestones table.

    Upsert key: (project_id, name) — running twice is safe.

    Returns:
        (loaded_count, errors)
    """
    df = pd.read_csv(csv_path, dtype=str, skip_blank_lines=True).dropna(how="all")

    loaded = 0
    errors: list[dict] = []

    for idx, row in df.iterrows():
        try:
            name = str(row["name"]).strip()
            if not name:
                continue

            planned = _parse_date(row.get("planned_date"))
            if planned is None:
                raise ValueError("planned_date is required")

            actual = _parse_date(row.get("actual_date"))

            pct_raw = str(row.get("completion_percentage", "0")).strip()
            pct = Decimal(pct_raw) if pct_raw else Decimal("0")

            existing = Milestone.query.filter_by(
                project_id=project.id, name=name
            ).first()

            if existing:
                existing.phase = str(row.get("phase", "")).strip() or None
                existing.planned_date = planned
                existing.actual_date = actual
                existing.completion_percentage = pct
                existing.notes = _str_or_none(row.get("notes"))
            else:
                m = Milestone(
                    project_id=project.id,
                    name=name,
                    phase=str(row.get("phase", "")).strip() or None,
                    planned_date=planned,
                    actual_date=actual,
                    completion_percentage=pct,
                    notes=_str_or_none(row.get("notes")),
                )
                db.session.add(m)

            loaded += 1

        except Exception as exc:
            errors.append({"row": int(idx) + 2, "error": str(exc)})

    db.session.flush()
    return loaded, errors
