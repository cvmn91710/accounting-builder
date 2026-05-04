"""Stage 2 — Gemini schedule categorization across all transactions."""

from __future__ import annotations

import json
import re
from typing import Any

import google.generativeai as genai

from app.config import get_settings
from app.models import CategorizationResult, ConfidenceLevel
from app.schedules import MatterType, matter_type_notes, schedules_prompt_block


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


def build_categorization_prompt(
    matter_type: str,
    transactions: list[dict[str, Any]],
) -> str:
    mt_key = _resolve_matter_key(matter_type)
    mt = matter_type.replace("_", " ")
    return f"""You are a California probate accounting specialist.

Matter type: {mt}
Context: {matter_type_notes(mt_key)}

Schedule definitions:
{schedules_prompt_block()}

Tasks:
- Assign each transaction to exactly one schedule letter A through I, OR use special values:
  - "internal_transfer" for transfers between accounts owned by the same matter (exclude from ordinary schedules; still audit).
  - "needs_review" when classification is ambiguous.
- Provide confidence: high, medium, or low for each.
- Brief reasoning for medium/low.
- subcategory: short label when helpful (e.g. Utilities, Interest Income, personal_needs).

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
