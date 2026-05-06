"""Stage 1 — Gemini transaction extraction."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Optional

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

Where to read transactions:
- If TABLES contains a large grid of dated transaction rows, use it as the primary source.
- If TABLES is short or only shows fees/summaries/checkboxes, ignore it for line items — extract EVERY transaction from the FULL PAGE TEXT section (e.g. Wells Fargo "Transaction history" printed as flowing text with dates and amounts).

Rules:
- Identify financial institution, account type, last 4 digits of account number only, and statement period (start/end dates).
- Extract EVERY posted transaction row from transaction history / activity tables — not header lines, not column titles, not a single summary "total" row unless it is the only line on the page.
- Many checking statements (e.g. Wells Fargo) use TWO amount columns: "Deposits/Additions" and "Withdrawals/Subtractions" (or similar). For each row:
  - If only one column has a value, set "amount" to that number, NEGATIVE for withdrawals/subtractions, POSITIVE for deposits/credits.
  - If both columns could apply, use deposit as positive and withdrawal as negative (one transaction row = one net movement).
- "amount" must be a single number per row when possible. "description" = full transaction description line as printed (all narrative text for the row).
- "payee" = counterparty / merchant / payee name when the statement shows it as a separate field OR when it can be inferred clearly from the description (e.g. leading name before a long reference string). Use null if there is no distinct payee or it would be a guess.
- Check number column: if present, you may prepend to description (e.g. "Check 1234 — ...") or omit if it breaks JSON; do not skip the row.
- For each transaction: type (debit/credit/transfer/check/etc.), running balance in "balance" if a balance column exists, sourcePage = 1-based page from the TABLES or TEXT label (e.g. "Page 3 table 1" => 3).
- For brokerage: also security symbol, quantity, price, cost basis if present.
- beginningBalance and endingBalance if shown on the statement.
- Return ONLY valid JSON — no markdown, no preamble. Use null for unknown fields; do not invent amounts.
- If the document clearly lists transactions but you return none, add a flag explaining why.
- If unsure about a field, use null and add a short note to flags[].
- Use JSON only: null (not NaN, not None, not undefined), true/false (not True/False), no trailing commas.

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
      "payee": string|null,
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


# Large statements need headroom; 8192 often truncates mid-JSON (unterminated strings).
_MAX_EXTRACT_OUTPUT_TOKENS = 65536


def _strip_json_fence(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _sanitize_gemini_json(s: str) -> str:
    """Repair common non-standard JSON emitted by models (still invalid for json.loads)."""
    out = s
    out = re.sub(r"\bNaN\b", "null", out, flags=re.IGNORECASE)
    out = re.sub(r"\bInfinity\b", "null", out)
    out = re.sub(r"\b-Infinity\b", "null", out)
    out = re.sub(r"\bNone\b", "null", out)
    out = re.sub(r"\bTrue\b", "true", out)
    out = re.sub(r"\bFalse\b", "false", out)
    out = re.sub(r"\bundefined\b", "null", out)
    out = re.sub(r":\s*,", ": null,", out)
    for _ in range(12):
        nxt = re.sub(r",(\s*[\]}])", r"\1", out)
        if nxt == out:
            break
        out = nxt
    return out


def _tables_likely_full_ledger(tables_text: str) -> bool:
    """Heuristic: pdfplumber often misses the real history table; small snippets are not the ledger."""
    if not tables_text or not tables_text.strip():
        return False
    lines = [ln for ln in tables_text.splitlines() if ln.strip()]
    if len(lines) < 18:
        return False
    pipe_rows = sum(
        1 for ln in lines if "|" in ln and ln.count("|") >= 3
    )
    return pipe_rows >= 10


def _debug_session_ndjson(
    hypothesis_id: str, location: str, message: str, data: dict[str, Any]
) -> None:
    # #region agent log
    roots = {Path(__file__).resolve().parent.parent, Path.cwd()}
    payload = {
        "sessionId": "7da9e7",
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    line = json.dumps(payload, default=str) + "\n"
    for root in roots:
        try:
            with (root / "debug-7da9e7.log").open("a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass
    # #endregion


def _response_finish_reason(response: Any) -> Optional[str]:
    try:
        cands = getattr(response, "candidates", None) or []
        if cands:
            return str(getattr(cands[0], "finish_reason", None))
    except Exception:
        pass
    return None


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
        "\n\n=== TABLES (structured extracts; may be incomplete) ===\n\n"
        + tables_text[:400_000]
        if tables_text
        else ""
    )
    text_part = "\n\n=== FULL PAGE TEXT ===\n\n" + combined_text[:900_000]
    if tables_text and _tables_likely_full_ledger(tables_text):
        body = EXTRACTION_PROMPT_HEADER + tables_part + text_part
    else:
        body = EXTRACTION_PROMPT_HEADER + text_part + tables_part

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
                max_output_tokens=_MAX_EXTRACT_OUTPUT_TOKENS,
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
    raw_stripped = _strip_json_fence(raw)
    fr = _response_finish_reason(response)
    # #region agent log
    _debug_session_ndjson(
        "H1",
        "gemini_extractor.py:post_response",
        "before_json_loads",
        {
            "max_output_tokens": _MAX_EXTRACT_OUTPUT_TOKENS,
            "response_text_len": len(response.text or ""),
            "raw_after_fence_len": len(raw_stripped),
            "finish_reason": fr,
            "starts_with_brace": (raw_stripped[:1] == "{"),
            "prefix_200": (raw_stripped[:200] if raw_stripped else ""),
            "suffix_200": (raw_stripped[-200:] if raw_stripped else ""),
        },
    )
    # #endregion

    def _log_json_err(
        err: json.JSONDecodeError, payload: str, tag: str
    ) -> None:
        pos = getattr(err, "pos", None)
        snip = ""
        if isinstance(pos, int) and payload:
            lo = max(0, pos - 100)
            hi = min(len(payload), pos + 100)
            snip = payload[lo:hi]
        _debug_session_ndjson(
            "H2",
            "gemini_extractor.py:json_loads",
            tag,
            {
                "error": str(err),
                "pos": pos,
                "lineno": getattr(err, "lineno", None),
                "colno": getattr(err, "colno", None),
                "finish_reason": fr,
                "raw_len": len(payload),
                "snippet_around_pos": snip,
                "suffix_400": payload[-400:] if payload else "",
            },
        )

    data: Optional[dict[str, Any]] = None
    parse_mode = "direct"
    try:
        data = json.loads(raw_stripped)
    except json.JSONDecodeError as e_first:
        _log_json_err(e_first, raw_stripped, "JSONDecodeError_first_pass")
        sanitized = _sanitize_gemini_json(raw_stripped)
        try:
            data = json.loads(sanitized)
            parse_mode = "sanitized"
            _debug_session_ndjson(
                "H2",
                "gemini_extractor.py:json_loads",
                "recovered_after_sanitize",
                {"finish_reason": fr, "sanitized_len": len(sanitized)},
            )
        except json.JSONDecodeError as e2:
            _log_json_err(e2, sanitized, "JSONDecodeError_after_sanitize")
            raise e2 from e_first
    # #region agent log
    assert data is not None
    _debug_session_ndjson(
        "H5",
        "gemini_extractor.py:json_loads",
        "json_ok",
        {
            "parse_mode": parse_mode,
            "txn_count": len(data.get("transactions") or []),
            "finish_reason": fr,
        },
    )
    # #endregion
    data = _coerce_extraction_dict(data)
    return ExtractionResult.model_validate(data)
