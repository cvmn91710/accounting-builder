"""Stage 1 — Gemini transaction extraction."""

from __future__ import annotations

import json
import re
from typing import Any

import google.generativeai as genai

from app.config import get_settings
from app.models import DocumentType, DocumentTypeDetection, ExtractionResult


# --- Document type detection (small, fast call before the main extraction) ---

DOCUMENT_TYPE_PROMPT = """You are a financial document classifier. Read the snippet below from page 1 of a statement and identify the document type.

Return ONLY valid JSON:
{
  "documentType": "bank" | "brokerage" | "credit_card" | "retirement" | "unknown",
  "institution": string|null,
  "confidence": "high" | "medium" | "low"
}

Type guide:
- "brokerage": investment / securities account. Shows holdings (positions) with shares, market value, cost basis; buy/sell trades; dividend / interest / capital gain distributions (Schwab, Fidelity, Vanguard, Merrill, Morgan Stanley, Edward Jones, etc.).
- "bank": checking, savings, money market deposit account at a bank. Shows deposits/withdrawals/checks.
- "credit_card": credit-card statement with purchases, payments, minimum due, credit limit.
- "retirement": IRA / 401(k) / pension / annuity statement (may resemble brokerage; classify "retirement" if it explicitly says IRA / 401k / Roth / pension / annuity).
- "unknown": cannot tell.

Document snippet:
"""


def _strip_json_fence(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def detect_document_type(combined_text: str) -> DocumentTypeDetection:
    """Classify the statement type with a small Gemini call. Falls back to 'unknown' on error."""
    settings = get_settings()
    if not settings.gemini_api_key:
        return DocumentTypeDetection(document_type=DocumentType.unknown)

    snippet = (combined_text or "")[:8000]
    if not snippet.strip():
        return DocumentTypeDetection(document_type=DocumentType.unknown)

    try:
        genai.configure(api_key=settings.gemini_api_key)
        model = genai.GenerativeModel(settings.gemini_model)
        response = model.generate_content(
            DOCUMENT_TYPE_PROMPT + snippet,
            generation_config=genai.types.GenerationConfig(
                temperature=0,
                max_output_tokens=256,
                response_mime_type="application/json",
            ),
        )
        raw = response.text or "{}"
        data = json.loads(_strip_json_fence(raw))
    except Exception:
        return DocumentTypeDetection(document_type=DocumentType.unknown)

    try:
        return DocumentTypeDetection.model_validate(data)
    except Exception:
        return DocumentTypeDetection(document_type=DocumentType.unknown)


# --- Main extraction prompts ---

_COMMON_JSON_RULES = """- Use JSON only: null (not NaN, not None, not undefined), true/false (not True/False), no trailing commas.
- Return ONLY valid JSON — no markdown, no preamble. Use null for unknown fields; do not invent amounts.
- If unsure about a field, use null and add a short note to flags[].
"""


BANK_PROMPT_HEADER = """You are a financial document analyst. Extract structured data from the bank/credit-card/retirement statement below.

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
- beginningBalance and endingBalance if shown on the statement.
- If the document clearly lists transactions but you return none, add a flag explaining why.
""" + _COMMON_JSON_RULES + """
JSON shape:
{
  "documentType": "bank" | "credit_card" | "retirement" | "unknown",
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
      "sourcePage": number|null
    }
  ],
  "flags": [string]
}

Statement text and tables:
"""


BROKERAGE_PROMPT_HEADER = """You are a financial document analyst working on California probate / trust / conservatorship accountings. Extract structured data from the BROKERAGE / INVESTMENT statement below.

A brokerage statement has THREE kinds of data to extract:
1. beginningHoldings — positions held at the START of the statement period (often labeled "Beginning Positions", "Holdings as of [start date]", or pulled from the prior month's end value).
2. endingHoldings — positions held at the END of the statement period ("Holdings", "Positions", "Account Holdings", with shares/units, market value, cost basis).
3. transactions — every activity row during the period (trades, dividends, interest, capital-gain distributions, fees, cash deposits/withdrawals, transfers in/out).

Identification:
- Identify the financial institution, account type (e.g., Individual Brokerage / IRA / Trust), last 4 digits of the account number, and statement period.
- beginningBalance / endingBalance = the CASH balance only (sweep money market / available cash). Do NOT put total account value here.

Holdings rules (both beginningHoldings and endingHoldings):
- One row per security or cash position.
- "assetClass":
  - "cash" for cash, sweep accounts, money market funds explicitly held as the cash position (e.g. "SCHWAB BANK MONEY MARKET", "FIDELITY GOVERNMENT MONEY MARKET FUND - SPAXX")
  - "non_cash" for everything else (stocks, bonds, ETFs, mutual funds)
- securitySymbol = ticker / CUSIP / fund symbol when shown; null otherwise.
- securityDescription = full security name as printed.
- quantity = shares / units / face value.
- unitPrice = per-share price on the statement valuation date.
- marketValue = total fair-market value for that lot on the valuation date.
- costBasis = cost basis if shown; null if not (do NOT guess).
- sourcePage = 1-based page number.

Transaction rules:
- One row per posted activity.
- "tradeKind" — pick the best fit:
  - "buy" — purchase of a security (including reinvested-dividend buys).
  - "sell" — sale of a security.
  - "dividend" — cash or reinvested dividend income.
  - "interest" — interest income credited to the account.
  - "cap_gain_dist" — capital-gain distribution from a mutual fund/ETF (separate from realized gains on a sale).
  - "fee" — account / advisory / commission / wire fee.
  - "transfer_in" — securities or cash transferred INTO the account.
  - "transfer_out" — securities or cash transferred OUT.
  - "cash" — bank-style cash movement that does not fit above.
  - "other" — anything else; explain in flags.
- amount = net cash effect on the account: NEGATIVE for cash leaving the account (buys, fees, withdrawals, transfer_out), POSITIVE for cash arriving (sells, dividends, interest, deposits, transfer_in). For non-cash transfers (in-kind) leave amount null.
- For BUY/SELL trades: also fill securitySymbol, quantity, price, costBasis (cost of the lot purchased OR sold), proceeds (gross proceeds on a sale), and realizedGainLoss (proceeds minus cost basis, signed) when the statement reports a realized gain/loss.
- REINVESTED DIVIDENDS: emit TWO rows — one "dividend" (positive cash) and one "buy" (negative cash of equal magnitude) with the security and shares purchased.
- description = the full activity description as printed.
- type = the original statement's transaction-type label (free-form, e.g. "DIV", "BUY", "JOURNAL").
- sourcePage = 1-based page number.

""" + _COMMON_JSON_RULES + """
JSON shape:
{
  "documentType": "brokerage",
  "institution": string|null,
  "accountType": string|null,
  "accountNumberLast4": string|null,
  "statementPeriodStart": "YYYY-MM-DD"|null,
  "statementPeriodEnd": "YYYY-MM-DD"|null,
  "beginningBalance": number|null,
  "endingBalance": number|null,
  "beginningHoldings": [
    {
      "assetClass": "cash"|"non_cash",
      "securitySymbol": string|null,
      "securityDescription": string|null,
      "quantity": number|null,
      "unitPrice": number|null,
      "marketValue": number|null,
      "costBasis": number|null,
      "sourcePage": number|null
    }
  ],
  "endingHoldings": [ /* same shape as beginningHoldings */ ],
  "transactions": [
    {
      "date": "YYYY-MM-DD"|null,
      "description": string|null,
      "payee": string|null,
      "amount": number|null,
      "type": string|null,
      "tradeKind": "buy"|"sell"|"dividend"|"interest"|"cap_gain_dist"|"fee"|"transfer_in"|"transfer_out"|"cash"|"other",
      "securitySymbol": string|null,
      "quantity": number|null,
      "price": number|null,
      "costBasis": number|null,
      "proceeds": number|null,
      "realizedGainLoss": number|null,
      "balance": number|null,
      "sourcePage": number|null
    }
  ],
  "flags": [string]
}

Statement text and tables:
"""


# Large statements need headroom; 8192 often truncates mid-JSON (unterminated strings).
_MAX_EXTRACT_OUTPUT_TOKENS = 65536


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


def _coerce_extraction_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize keys for Pydantic."""
    txns = data.get("transactions") or []
    data["transactions"] = [t for t in txns if isinstance(t, dict)]
    for k in ("beginningHoldings", "endingHoldings", "beginning_holdings", "ending_holdings"):
        v = data.get(k)
        if v is None:
            continue
        data[k] = [p for p in v if isinstance(p, dict)]
    return data


def extract_statement_with_gemini(
    combined_text: str,
    tables_text: str,
    *,
    document_type: DocumentType = DocumentType.unknown,
) -> ExtractionResult:
    """Run the main extraction. When `document_type == brokerage`, use the brokerage prompt
    so beginning/ending holdings are also captured. Otherwise fall back to the bank prompt."""
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
    prompt_header = (
        BROKERAGE_PROMPT_HEADER
        if document_type == DocumentType.brokerage
        else BANK_PROMPT_HEADER
    )
    if tables_text and _tables_likely_full_ledger(tables_text):
        body = prompt_header + tables_part + text_part
    else:
        body = prompt_header + text_part + tables_part

    response = model.generate_content(
        body,
        generation_config=genai.types.GenerationConfig(
            temperature=0,
            max_output_tokens=_MAX_EXTRACT_OUTPUT_TOKENS,
            response_mime_type="application/json",
        ),
    )
    raw = response.text or "{}"
    raw_stripped = _strip_json_fence(raw)
    try:
        data = json.loads(raw_stripped)
    except json.JSONDecodeError as e_first:
        try:
            data = json.loads(_sanitize_gemini_json(raw_stripped))
        except json.JSONDecodeError as e2:
            raise e2 from e_first

    data = _coerce_extraction_dict(data)
    # Ensure documentType is set even if the model omits it.
    if not data.get("documentType") and not data.get("document_type"):
        data["documentType"] = document_type.value
    return ExtractionResult.model_validate(data)
