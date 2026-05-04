"""Load and validate JSON template mapping for Excel output."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field


class ScheduleRangeConfig(BaseModel):
    sheet: str
    first_data_row: int = Field(ge=1)
    columns: dict[str, str]  # date, description, amount, payee, category, notes -> column letters


class TemplateMappingFile(BaseModel):
    """Per spec: one mapping file can describe all matter types or nested keys."""

    conservatorship: Optional[dict[str, ScheduleRangeConfig]] = None
    probate_estate: Optional[dict[str, ScheduleRangeConfig]] = None
    trust_administration: Optional[dict[str, ScheduleRangeConfig]] = None
    # Flat fallback: schedule_letter -> config
    schedules: Optional[dict[str, ScheduleRangeConfig]] = None
    matter_metadata_cells: dict[str, str] = Field(default_factory=dict)


def load_template_mapping(path: Path) -> TemplateMappingFile:
    data = json.loads(path.read_text(encoding="utf-8"))
    return TemplateMappingFile.model_validate(data)


def schedules_for_matter(
    mapping: TemplateMappingFile, matter_type: str
) -> dict[str, ScheduleRangeConfig]:
    m = matter_type.lower().replace(" ", "_")
    if "conservator" in m and mapping.conservatorship:
        return dict(mapping.conservatorship)
    if "trust" in m and mapping.trust_administration:
        return dict(mapping.trust_administration)
    if mapping.probate_estate and ("probate" in m or m == "probate_estate"):
        return dict(mapping.probate_estate)
    if mapping.schedules:
        return dict(mapping.schedules)
    return {}
