"""Cross-statement reconciliation: duplicates, transfers, balance checks."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Optional

from app.models import ReconciliationIssue, ReconciliationIssueType


def _d(v: Any) -> Optional[Decimal]:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except Exception:
        return None


def run_reconciliation(
    statements: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
) -> list[ReconciliationIssue]:
    """Deterministic checks. statements: id, account_last4, period_start, period_end, beginning_balance, ending_balance, institution."""
    issues: list[ReconciliationIssue] = []

    # Index txns by statement
    by_stmt: dict[str, list[dict]] = defaultdict(list)
    for t in transactions:
        sid = t.get("statement_id")
        if sid:
            by_stmt[sid].append(t)

    # Duplicate detection (same account / statement)
    for sid, txns in by_stmt.items():
        seen: dict[tuple, list[str]] = {}
        for t in txns:
            tid = t.get("id")
            d = t.get("txn_date")
            if hasattr(d, "isoformat"):
                d = d.isoformat()
            amt = _d(t.get("amount"))
            desc = (t.get("description") or "").strip().upper()
            if amt is None or not d:
                continue
            key = (str(d), str(amt), desc, sid)
            seen.setdefault(key, []).append(tid)
        for key, ids in seen.items():
            if len(ids) > 1:
                issues.append(
                    ReconciliationIssue(
                        type=ReconciliationIssueType.duplicate,
                        message=f"Potential duplicate transactions on same statement: {key}",
                        transaction_ids=[i for i in ids if i],
                    )
                )

    # Internal transfer: opposite signs, same |amount|, date within ±1 day, different statements
    flat = [t for t in transactions if _d(t.get("amount")) is not None]
    for i, a in enumerate(flat):
        amta = _d(a["amount"])
        if amta is None or amta == 0:
            continue
        da = a.get("txn_date")
        if isinstance(da, str):
            from datetime import datetime as dt

            try:
                da = date.fromisoformat(da[:10])
            except Exception:
                da = None
        if not da:
            continue
        for b in flat[i + 1 :]:
            if a.get("statement_id") == b.get("statement_id"):
                continue
            amtb = _d(b.get("amount"))
            if amtb is None:
                continue
            db = b.get("txn_date")
            if isinstance(db, str):
                try:
                    db = date.fromisoformat(db[:10])
                except Exception:
                    db = None
            if not db:
                continue
            if amta + amtb != 0:
                continue
            if abs((da - db).days) > 1:
                continue
            issues.append(
                ReconciliationIssue(
                    type=ReconciliationIssueType.internal_transfer,
                    message="Potential internal transfer (offsetting amounts, nearby dates across accounts)",
                    transaction_ids=[a.get("id"), b.get("id")],
                    meta={
                        "amount": str(abs(amta)),
                        "dates": [str(da), str(db)],
                    },
                )
            )

    # Balance chain per account (institution + last 4)
    groups: dict[str, list[dict]] = defaultdict(list)
    for s in statements:
        key = f"{(s.get('institution') or '').upper()}|{s.get('account_last4') or ''}"
        groups[key].append(s)

    for key, stmts in groups.items():
        stmts_sorted = sorted(
            stmts,
            key=lambda x: (x.get("statement_period_start") or date.min, x.get("id")),
        )
        for prev, nxt in zip(stmts_sorted, stmts_sorted[1:]):
            end_prev = _d(prev.get("ending_balance"))
            beg_next = _d(nxt.get("beginning_balance"))
            if end_prev is not None and beg_next is not None and end_prev != beg_next:
                issues.append(
                    ReconciliationIssue(
                        type=ReconciliationIssueType.balance_mismatch,
                        message=f"Beginning balance of next statement does not match ending balance (key {key})",
                        amount_delta=beg_next - end_prev,
                        transaction_ids=[],
                        meta={
                            "previous_statement_id": prev.get("id"),
                            "next_statement_id": nxt.get("id"),
                        },
                    )
                )

    return issues


def issues_to_json(issues: list[ReconciliationIssue]) -> str:
    return json.dumps([i.model_dump(mode="json") for i in issues], default=str)


def apply_internal_transfer_flags(
    transactions: list[dict[str, Any]],
    issues: list[ReconciliationIssue],
) -> None:
    """Mark transactions involved in internal_transfer issues (mutates dicts in place)."""
    for iss in issues:
        if iss.type != ReconciliationIssueType.internal_transfer:
            continue
        for tid in iss.transaction_ids:
            if not tid:
                continue
            for t in transactions:
                if t.get("id") == tid:
                    t["internal_transfer"] = True
                    t["schedule"] = "internal_transfer"
                    break
