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


EXTRACTION_PROMPT_HEADER = """You are a financial document analyst. Extract structured data from the bank/brokerage/credit card/retirement statement below.

Priority: The TABLES section (if present) is the authoritative source for transaction rows — use it first, then the full-page text for anything missing.

Rules:
- Identify financial institution, account type, last 4 digits of account number only, and statement period (start/end dates).
- Extract EVERY posted transaction row from transaction history / activity tables — not header lines, not column titles, not a single summary "total" row unless it is the only line on the page.
- Many checking statements (e.g. Wells Fargo) use TWO amount columns: "Deposits/Additions" and "Withdrawals/Subtractions" (or similar). For each row:
  - If only one column has a value, set "amount" to that number, NEGATIVE for withdrawals/subtractions, POSITIVE for deposits/credits.
  - If both columns could apply, use deposit as positive and withdrawal as negative (one transaction row = one net movement).
- "amount" must be a single number per row when possible. "description" = text from the description/payee column as printed.
- Check number column: if present, you may prepend to description (e.g. "Check 1234 — ...") or omit if it breaks JSON; do not skip the row.
- For each transaction: type (debit/credit/transfer/check/etc.), running balance in "balance" if a balance column exists, sourcePage = 1-based page from the TABLES or TEXT label (e.g. "Page 3 table 1" => 3).
- For brokerage: also security symbol, quantity, price, cost basis if present.
- beginningBalance and endingBalance if shown on the statement.
- Return ONLY valid JSON — no markdown, no preamble. Use null for unknown fields; do not invent amounts.
- If the document clearly lists transactions but you return none, add a flag explaining why.
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

    tables_part = (
        "\n\n=== TABLES (extract every data row into transactions[]) ===\n\n" + tables_text[:400_000]
        if tables_text
        else ""
    )
    text_part = (
        "\n\n=== FULL PAGE TEXT ===\n\n" + combined_text[:900_000]
    )
    body = EXTRACTION_PROMPT_HEADER + tables_part + text_part

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
