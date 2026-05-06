"""Post-extraction: normalize bank/brokerage transaction descriptions with Gemini."""

from __future__ import annotations

import json
import re
from typing import Any

import google.generativeai as genai

from app.config import get_settings
from app.models import ConfidenceLevel, DescriptionCleanupResult


def _strip_json_fence(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def build_description_cleanup_prompt(transactions: list[dict[str, Any]]) -> str:
    return f"""You clean transaction descriptions from bank and brokerage statements for California probate/trust accounting.

Rules:
- Produce a concise, human-readable description: normalize merchant/payee names, remove redundant reference numbers where safe, fix obvious OCR/garbling.
- Do NOT invent a counterparty or payee name that is not clearly implied by the raw text.
- Preserve factual content (check numbers, wire refs) when they identify the transaction.
- If the raw text is too ambiguous to clean reliably, set confidence to "low" and keep cleanedDescription close to the original or slightly normalized.
- confidence per row: "high" only when the cleaned text is clearly right; "medium" when reasonable but not certain; "low" when ambiguous or unreadable.

Return ONLY valid JSON:
{{ "cleanups": [
  {{
    "transactionId": "<id>",
    "cleanedDescription": "string",
    "confidence": "high"|"medium"|"low",
    "reasoning": "short text or null"
  }}
]}}

Transactions (JSON array):
{json.dumps(transactions, default=str)[:1_500_000]}
"""


def cleanup_descriptions_with_gemini(
    transactions: list[dict[str, Any]],
) -> DescriptionCleanupResult:
    settings = get_settings()
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(settings.gemini_model)

    prompt = build_description_cleanup_prompt(transactions)
    response = model.generate_content(
        prompt,
        generation_config=genai.types.GenerationConfig(
            temperature=0,
            max_output_tokens=8192,
            response_mime_type="application/json",
        ),
    )
    raw = response.text or "{}"
    raw = _strip_json_fence(raw)
    data = json.loads(raw)
    items = data.get("cleanups") or []
    norm: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        conf = item.get("confidence") or "medium"
        try:
            item["confidence"] = ConfidenceLevel(conf)
        except ValueError:
            item["confidence"] = ConfidenceLevel.medium
        norm.append(item)
    data["cleanups"] = norm
    return DescriptionCleanupResult.model_validate(data)
