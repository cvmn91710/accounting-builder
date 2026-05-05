"""Load and validate JSON template mapping for Excel output (legacy flat map + spec v1.2 master template)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, Union

from pydantic import BaseModel, Field, model_validator


class ScheduleRangeConfig(BaseModel):
    sheet: str
    first_data_row: int = Field(ge=1)
    columns: dict[str, str]  # date, description, amount, payee, category, notes -> column letters


class TemplateMappingFile(BaseModel):
    """Legacy: flat schedule letter → sheet/range (pre–v1.2 deployments)."""

    conservatorship: Optional[dict[str, ScheduleRangeConfig]] = None
    probate_estate: Optional[dict[str, ScheduleRangeConfig]] = None
    trust_administration: Optional[dict[str, ScheduleRangeConfig]] = None
    schedules: Optional[dict[str, ScheduleRangeConfig]] = None
    matter_metadata_cells: dict[str, str] = Field(default_factory=dict)


class AdHocScheduleMeta(BaseModel):
    """Spec v1.2 — metadata when adding D / G / I / K / L / P / X sheets."""

    model_config = {"populate_by_name": True, "extra": "allow"}

    label: str
    add_to_working_balance: Optional[str] = Field(default=None, alias="addToWorkingBalance")


class MasterTemplateMappingV12(BaseModel):
    """Spec v1.2 — single master workbook mapping (`sheets` block)."""

    model_config = {"populate_by_name": True, "extra": "ignore"}

    template_path: Optional[str] = Field(default=None, alias="templatePath")
    sheets: dict[str, Any]
    ad_hoc_schedules: dict[str, AdHocScheduleMeta] = Field(
        default_factory=dict, alias="adHocSchedules"
    )

    @model_validator(mode="before")
    @classmethod
    def _drop_adhoc_doc_keys(cls, data: Any) -> Any:
        """JSON may include adHocSchedules._note (string); only letter → object entries are real."""
        if not isinstance(data, dict):
            return data
        raw = data.get("adHocSchedules")
        if not isinstance(raw, dict):
            return data
        cleaned = {
            k: v
            for k, v in raw.items()
            if not str(k).startswith("_") and isinstance(v, dict)
        }
        return {**data, "adHocSchedules": cleaned}


def is_v12_mapping_dict(data: dict[str, Any]) -> bool:
    sheets = data.get("sheets")
    return isinstance(sheets, dict) and "workingBalance" in sheets


def load_template_mapping(path: Path) -> TemplateMappingFile:
    data = json.loads(path.read_text(encoding="utf-8"))
    return TemplateMappingFile.model_validate(data)


def load_mapping_any(path: Path) -> Union[TemplateMappingFile, MasterTemplateMappingV12]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if is_v12_mapping_dict(data):
        return MasterTemplateMappingV12.model_validate(data)
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
