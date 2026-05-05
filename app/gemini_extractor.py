"""Stage 1 — Gemini transaction extraction."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any

import google.generativeai as genai

from app.config import get_settings
from app.debug_agent_log import agent_debug_log
from app.models import ExtractionResult


def _google_genai_package_version() -> str:
    try:
        from importlib import metadata

        return metadata.version("google-generativeai")
    except Exception:
        return "unknown"


EXTRACTION_PROMPT_HEADER = """You are a financial document analyst. Extract structured data from the bank/brokerage/credit card/retirement statement text below.

Rules:
- Identify financial institution, account type, last 4 digits of account number only, and statement period (start/end dates).
- Extract EVERY transaction: date, description as printed, amount (signed: inflows positive, outflows negative where the statement uses that convention; preserve sign as shown).
- For each transaction: type (debit/credit/interest/dividend/fee/transfer/trade/etc.), running balance if present, sourcePage = 1-based PDF page number where the transaction appears.
- For brokerage: also security symbol, quantity, price, cost basis if present.
- beginningBalance and endingBalance if shown.
- Return ONLY valid JSON — no markdown, no preamble. Use null for unknown fields; do not invent amounts.
- If unsure about a field, use null and add a short note to flags[].

JSON shape:
{
  "institution": string|null,
  "accountType": string|null,
  "accountNumberLast4": string|null,
  "statementPeriodStart": "YYYY-MM-DD"|null,
  "statementPeriodEnd": "YYYY-MM-DD"|null,
  "beginningBalance": number|null,
  "endingBalance": number|null,
  "transactions": [
    {
      "date": "YYYY-MM-DD"|null,
      "description": string|null,
      "amount": number|null,
      "type": string|null,
      "balance": number|null,
      "sourcePage": number|null,
      "securitySymbol": string|null,
      "quantity": number|null,
      "price": number|null,
      "costBasis": number|null
    }
  ],
  "flags": [string]
}

Statement text and tables:
"""


def _strip_json_fence(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _coerce_extraction_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize keys for Pydantic."""
    txns = data.get("transactions") or []
    out_tx = []
    for t in txns:
        if not isinstance(t, dict):
            continue
        out_tx.append(t)
    data["transactions"] = out_tx
    return data


def extract_statement_with_gemini(combined_text: str, tables_text: str) -> ExtractionResult:
    settings = get_settings()
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(settings.gemini_model)

    body = (
        EXTRACTION_PROMPT_HEADER
        + "\n"
        + combined_text[:1_200_000]
        + ("\n\nTABLES:\n" + tables_text[:200_000] if tables_text else "")
    )

    # #region agent log
    agent_debug_log(
        "gemini_extractor.py:extract_statement_with_gemini",
        "pre_generate_content",
        {
            "resolved_gemini_model": settings.gemini_model,
            "google_generativeai_version": _google_genai_package_version(),
            "api_key_configured": bool(settings.gemini_api_key),
            "prompt_body_chars": len(body),
        },
        "H1",
    )
    # #endregion

    # #region agent log
    try:
        response = model.generate_content(
            body,
            generation_config=genai.types.GenerationConfig(
                temperature=0,
                max_output_tokens=8192,
                response_mime_type="application/json",
            ),
        )
    except Exception as e:
        agent_debug_log(
            "gemini_extractor.py:extract_statement_with_gemini",
            "generate_content_exception",
            {
                "error_type": type(e).__name__,
                "error_message": str(e)[:4000],
            },
            "H2",
        )
        raise
    # #endregion
    raw = response.text or "{}"
    raw = _strip_json_fence(raw)
    data = json.loads(raw)
    data = _coerce_extraction_dict(data)
    return ExtractionResult.model_validate(data)
