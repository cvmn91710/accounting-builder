"""Stage 2 — Gemini schedule categorization across all transactions."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import google.generativeai as genai

from app.config import get_settings
from app.models import CategorizationResult, ConfidenceLevel
from app.schedules import (
    AD_HOC_SCHEDULE_LETTERS,
    MatterType,
    STANDARD_SCHEDULE_LETTERS,
    matter_type_notes,
    schedules_prompt_block,
)


def _strip_json_fence(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _resolve_matter_key(matter_type: str) -> MatterType:
    mtl = matter_type.lower()
    if "conservator" in mtl:
        return "conservatorship"
    if "trust" in mtl:
        return "trust_administration"
    return "probate_estate"


def _schedule_c_taxonomy_prompt_block() -> str:
    """Return condensed Schedule C taxonomy guidance for the model prompt."""
    taxonomy_path = Path(__file__).resolve().parent.parent / "Schedule_C_Master_Taxonomy.md"
    if not taxonomy_path.exists():
        return ""

    raw = taxonomy_path.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines()

    keep: list[str] = []
    in_design_notes = False
    for line in lines:
        s = line.rstrip()
        if not s:
            continue
        if s.startswith("## "):
            keep.append(s)
            in_design_notes = s.lower().startswith("## design notes")
            continue
        if in_design_notes:
            if s.startswith(("1.", "2.", "3.", "4.", "5.", "6.")) or s.startswith("- "):
                keep.append(s)
            continue
        if s.startswith(("- ", "**Convention:**", "**Matter-type applicability:**")):
            keep.append(s)

    condensed = "\n".join(keep).strip()
    if not condensed:
        return ""

    return (
        "Schedule C — Master Disbursement Taxonomy (authoritative guidance)\n"
        + condensed[:30_000]
        + "\n"
    )


def build_categorization_prompt(
    matter_type: str,
    transactions: list[dict[str, Any]],
) -> str:
    mt_key = _resolve_matter_key(matter_type)
    mt = matter_type.replace("_", " ")
    schedule_c_taxonomy = _schedule_c_taxonomy_prompt_block()
    return f"""You are a California probate accounting specialist.

Matter type: {mt}
Context: {matter_type_notes(mt_key)}

Schedule definitions:
{schedules_prompt_block()}

{schedule_c_taxonomy}

Tasks:
- Each transaction may include **payee** (counterparty) when provided — use it with **description** for classification.
- Assign each transaction to exactly one schedule letter from the firm's template scheme, OR use special values:
  - Standard sheets (always in master workbook): {", ".join(sorted(STANDARD_SCHEDULE_LETTERS))}.
  - Ad-hoc sheets (only when facts warrant — prefer standard schedules when they fit): {", ".join(sorted(AD_HOC_SCHEDULE_LETTERS))}.
  - "internal_transfer" for transfers between accounts both held in the name of the estate/trust/conservatorship (exclude from schedule totals; still audit).
  - "needs_review" when classification is ambiguous — do not guess.
- Provide confidence: high, medium, or low for each.
- Brief reasoning for medium/low.
- subcategory: must match an existing subcategory header on the target sheet when possible (e.g. Schedule A: Interest; Schedule C: Living Expenses). Use a clear provisional label if unsure and rely on staff review.

Return ONLY valid JSON:
{{ "categorizations": [
  {{
    "transactionId": "<id>",
    "schedule": "B",
    "subcategory": "Interest Income"|null,
    "confidence": "high"|"medium"|"low",
    "reasoning": "short text"|null
  }}
]}}

Transactions (JSON array):
{json.dumps(transactions, default=str)[:1_500_000]}
"""


def categorize_with_gemini(matter_type: str, transactions: list[dict[str, Any]]) -> CategorizationResult:
    settings = get_settings()
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(settings.gemini_model)

    prompt = build_categorization_prompt(matter_type, transactions)

    response = model.generate_content(
        prompt,
        generation_config=genai.types.GenerationConfig(
            temperature=0,
            max_output_tokens=4096,
            response_mime_type="application/json",
        ),
    )
    raw = response.text or "{}"
    raw = _strip_json_fence(raw)
    data = json.loads(raw)
    cats = data.get("categorizations") or []
    norm = []
    for c in cats:
        if not isinstance(c, dict):
            continue
        conf = c.get("confidence") or "medium"
        try:
            c["confidence"] = ConfidenceLevel(conf)
        except ValueError:
            c["confidence"] = ConfidenceLevel.medium
        norm.append(c)
    data["categorizations"] = norm
    return CategorizationResult.model_validate(data)
