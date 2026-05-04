"""Create minimal placeholder Excel templates and mapping if missing (dev only)."""

from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook

from app.config import get_settings


def _minimal_mapping() -> dict:
    cols = {"date": "A", "description": "B", "amount": "C", "payee": "D", "category": "E", "notes": "F"}
    schedules = {}
    for letter in list("ABCDEFGHI"):
        schedules[letter] = {
            "sheet": f"Sch_{letter}",
            "first_data_row": 4,
            "columns": cols,
        }
    return {
        "schedules": schedules,
        "probate_estate": schedules,
        "conservatorship": schedules,
        "trust_administration": schedules,
        "matter_metadata_cells": {},
    }


def ensure_placeholder_templates() -> None:
    s = get_settings()
    mapping_path = s.template_mapping_path
    if not mapping_path.parent.exists():
        mapping_path.parent.mkdir(parents=True, exist_ok=True)
    if not mapping_path.exists():
        mapping_path.write_text(json.dumps(_minimal_mapping(), indent=2), encoding="utf-8")

    for path in (
        s.template_path_conservatorship,
        s.template_path_probate,
        s.template_path_trust,
    ):
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        wb = Workbook()
        cover = wb.active
        cover.title = "Cover"
        cover["A1"] = "Golden Oaks — Placeholder Template"
        cover["A2"] = "Replace with firm master template and update template_mapping.json"
        cols = ["Date", "Description", "Amount", "Payee", "Category", "Notes"]
        for letter in list("ABCDEFGHI"):
            ws = wb.create_sheet(f"Sch_{letter}")
            ws["A1"] = f"Schedule {letter}"
            for i, h in enumerate(cols, start=1):
                ws.cell(row=3, column=i, value=h)
        wb.save(path)
