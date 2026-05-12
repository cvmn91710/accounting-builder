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


_REALIZED_GL_TOLERANCE = Decimal("0.05")
_HOLDINGS_QTY_TOLERANCE = Decimal("0.0001")
_HOLDINGS_COST_BASIS_TOLERANCE = Decimal("1.00")


def run_reconciliation(
    statements: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
    positions: Optional[list[dict[str, Any]]] = None,
) -> list[ReconciliationIssue]:
    """Deterministic checks. statements: id, account_last4, period_start, period_end, beginning_balance, ending_balance, institution.

    positions (optional): list of dicts with keys statement_id, as_of ('beginning'|'ending'),
    security_symbol, security_description, quantity, cost_basis — used for brokerage-statement
    carry-forward checks across consecutive statements on the same account.
    """
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

    # Holdings carry-forward: previous statement's ENDING positions should match
    # the next statement's BEGINNING positions on the same (institution, account_last4).
    if positions:
        issues.extend(_holdings_carry_forward_issues(groups, positions))

    # Realized gain/loss self-consistency: proceeds - cost_basis ~= realized_gain_loss.
    issues.extend(_realized_gain_loss_issues(transactions))

    return issues


def _index_positions(positions: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Return {(statement_id, as_of): [rows]}."""
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for p in positions:
        sid = p.get("statement_id")
        as_of = (p.get("as_of") or "").lower()
        if not sid or as_of not in ("beginning", "ending"):
            continue
        by_key[(sid, as_of)].append(p)
    return by_key


def _position_signature(p: dict[str, Any]) -> str:
    """Identify a holding by symbol when present, otherwise by description."""
    sym = (p.get("security_symbol") or "").strip().upper()
    if sym:
        return f"sym:{sym}"
    desc = (p.get("security_description") or "").strip().upper()
    return f"desc:{desc}" if desc else ""


def _holdings_carry_forward_issues(
    account_groups: dict[str, list[dict[str, Any]]],
    positions: list[dict[str, Any]],
) -> list[ReconciliationIssue]:
    issues: list[ReconciliationIssue] = []
    idx = _index_positions(positions)
    for key, stmts in account_groups.items():
        stmts_sorted = sorted(
            stmts,
            key=lambda x: (x.get("statement_period_start") or date.min, x.get("id")),
        )
        for prev, nxt in zip(stmts_sorted, stmts_sorted[1:]):
            prev_end = idx.get((prev.get("id"), "ending")) or []
            nxt_begin = idx.get((nxt.get("id"), "beginning")) or []
            if not prev_end or not nxt_begin:
                continue
            prev_map = {sig: p for p in prev_end if (sig := _position_signature(p))}
            next_map = {sig: p for p in nxt_begin if (sig := _position_signature(p))}
            all_keys = set(prev_map) | set(next_map)
            for k in sorted(all_keys):
                a = prev_map.get(k)
                b = next_map.get(k)
                if a is None:
                    issues.append(
                        ReconciliationIssue(
                            type=ReconciliationIssueType.holdings_mismatch,
                            message=(
                                f"Holding present at start of {nxt.get('id')} but absent "
                                f"at end of prior statement ({key}, {k})"
                            ),
                            meta={
                                "account_key": key,
                                "position": k,
                                "previous_statement_id": prev.get("id"),
                                "next_statement_id": nxt.get("id"),
                            },
                        )
                    )
                    continue
                if b is None:
                    issues.append(
                        ReconciliationIssue(
                            type=ReconciliationIssueType.holdings_mismatch,
                            message=(
                                f"Holding present at end of {prev.get('id')} but absent "
                                f"at start of next statement ({key}, {k})"
                            ),
                            meta={
                                "account_key": key,
                                "position": k,
                                "previous_statement_id": prev.get("id"),
                                "next_statement_id": nxt.get("id"),
                            },
                        )
                    )
                    continue
                qa = _d(a.get("quantity"))
                qb = _d(b.get("quantity"))
                if qa is not None and qb is not None and abs(qa - qb) > _HOLDINGS_QTY_TOLERANCE:
                    issues.append(
                        ReconciliationIssue(
                            type=ReconciliationIssueType.holdings_mismatch,
                            message=(
                                f"Quantity mismatch on {k} ({key}): "
                                f"end={qa}, next-begin={qb}"
                            ),
                            amount_delta=qb - qa,
                            meta={
                                "account_key": key,
                                "position": k,
                                "previous_statement_id": prev.get("id"),
                                "next_statement_id": nxt.get("id"),
                                "field": "quantity",
                            },
                        )
                    )
                ca = _d(a.get("cost_basis"))
                cb = _d(b.get("cost_basis"))
                if ca is not None and cb is not None and abs(ca - cb) > _HOLDINGS_COST_BASIS_TOLERANCE:
                    issues.append(
                        ReconciliationIssue(
                            type=ReconciliationIssueType.holdings_mismatch,
                            message=(
                                f"Cost-basis mismatch on {k} ({key}): "
                                f"end={ca}, next-begin={cb}"
                            ),
                            amount_delta=cb - ca,
                            meta={
                                "account_key": key,
                                "position": k,
                                "previous_statement_id": prev.get("id"),
                                "next_statement_id": nxt.get("id"),
                                "field": "cost_basis",
                            },
                        )
                    )
    return issues


def _realized_gain_loss_issues(
    transactions: list[dict[str, Any]],
) -> list[ReconciliationIssue]:
    issues: list[ReconciliationIssue] = []
    for t in transactions:
        kind = (t.get("trade_kind") or "").lower()
        if kind != "sell":
            continue
        proceeds = _d(t.get("proceeds"))
        cost_basis = _d(t.get("cost_basis"))
        gl = _d(t.get("realized_gain_loss"))
        if proceeds is None or cost_basis is None or gl is None:
            continue
        computed = proceeds - cost_basis
        if abs(computed - gl) > _REALIZED_GL_TOLERANCE:
            issues.append(
                ReconciliationIssue(
                    type=ReconciliationIssueType.realized_gain_loss_mismatch,
                    message=(
                        "Realized gain/loss does not equal proceeds - cost basis "
                        f"({proceeds} - {cost_basis} = {computed}, statement says {gl})"
                    ),
                    transaction_ids=[t.get("id")] if t.get("id") else [],
                    amount_delta=computed - gl,
                    meta={
                        "proceeds": str(proceeds),
                        "cost_basis": str(cost_basis),
                        "realized_gain_loss": str(gl),
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
