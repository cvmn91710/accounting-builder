"""Populate firm Excel template and append hidden audit trail sheet."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from openpyxl import load_workbook
from openpyxl.styles import Font
from openpyxl.utils import column_index_from_string

from app.config import matter_template_path
from app.template_config import load_template_mapping, schedules_for_matter


def _col_to_idx(letter: str) -> int:
    return column_index_from_string(letter.strip().upper())


def _write_row(
    ws,
    row: int,
    columns: dict[str, str],
    txn: dict[str, Any],
) -> None:
    if "date" in columns and txn.get("txn_date") is not None:
        d = txn["txn_date"]
        val = d.isoformat() if hasattr(d, "isoformat") else str(d)
        ws.cell(row=row, column=_col_to_idx(columns["date"]), value=val)
    if "description" in columns:
        ws.cell(
            row=row,
            column=_col_to_idx(columns["description"]),
            value=txn.get("description") or "",
        )
    if "amount" in columns and txn.get("amount") is not None:
        ws.cell(
            row=row,
            column=_col_to_idx(columns["amount"]),
            value=float(txn["amount"]),
        )
    if "payee" in columns:
        ws.cell(
            row=row,
            column=_col_to_idx(columns["payee"]),
            value=txn.get("payee") or "",
        )
    if "category" in columns:
        ws.cell(
            row=row,
            column=_col_to_idx(columns["category"]),
            value=txn.get("subcategory") or "",
        )
    if "notes" in columns:
        ws.cell(
            row=row,
            column=_col_to_idx(columns["notes"]),
            value=txn.get("notes") or "",
        )


def _normalize_schedule_letter(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = str(raw).strip().upper()
    if s in ("INTERNAL_TRANSFER", "EXCLUDED"):
        return None
    if s == "NEEDS_REVIEW" or s == "?":
        return None
    if len(s) >= 1 and s[0] in "ABCDEFGHI":
        return s[0]
    return None


def generate_accounting_workbook(
    matter_type: str,
    matter_name: str,
    period_start: date,
    period_end: date,
    transactions: list[dict[str, Any]],
    statement_by_id: dict[str, dict[str, Any]],
    mapping_path: Path,
    verifier_email: Optional[str],
) -> Path:
    template_path = matter_template_path(matter_type)
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")

    mapping = load_template_mapping(mapping_path)
    schedule_map = schedules_for_matter(mapping, matter_type)

    wb = load_workbook(template_path)

    # Build schedule buckets for worksheet rows (exclude internal_transfer / excluded from schedules)
    by_sched: dict[str, list[dict]] = {k: [] for k in schedule_map}
    for t in transactions:
        if t.get("excluded"):
            continue
        if t.get("internal_transfer"):
            continue
        letter = _normalize_schedule_letter(t.get("schedule"))
        if not letter or letter not in by_sched:
            continue
        by_sched[letter].append(t)

    written_row: dict[str, int] = {}
    for letter, cfg in schedule_map.items():
        sheet_name = cfg.sheet
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        txns = by_sched.get(letter, [])
        first = cfg.first_data_row
        for i, txn in enumerate(txns):
            r = first + i
            _write_row(ws, r, cfg.columns, txn)
            tid = txn.get("id")
            if tid:
                written_row[tid] = r

    audit_sheet = wb.create_sheet("_AuditTrail")
    audit_headers = [
        "Schedule",
        "RowInSchedule",
        "SourceFile",
        "SourcePage",
        "OriginalDescription",
        "Amount",
        "AIScheduleSuggestion",
        "AIConfidence",
        "FinalSchedule",
        "EditedByStaff",
        "VerifiedBy",
        "VerificationTimestamp",
    ]
    for c, h in enumerate(audit_headers, start=1):
        cell = audit_sheet.cell(row=1, column=c, value=h)
        cell.font = Font(bold=True)

    audit_row = 2
    for t in sorted(transactions, key=lambda x: (x.get("statement_id") or "", x.get("id") or "")):
        letter = _normalize_schedule_letter(t.get("schedule"))
        if t.get("internal_transfer") or t.get("excluded"):
            sched_display = "internal_transfer" if t.get("internal_transfer") else "excluded"
        else:
            sched_display = letter or (t.get("schedule") or "")
        tid = t.get("id")
        rin = written_row.get(tid, "")

        sid = t.get("statement_id")
        st = statement_by_id.get(sid, {})
        audit_sheet.cell(row=audit_row, column=1, value=sched_display)
        audit_sheet.cell(row=audit_row, column=2, value=rin)
        audit_sheet.cell(row=audit_row, column=3, value=st.get("original_filename", ""))
        audit_sheet.cell(row=audit_row, column=4, value=t.get("source_page"))
        audit_sheet.cell(row=audit_row, column=5, value=t.get("description", ""))
        amt = t.get("amount")
        audit_sheet.cell(
            row=audit_row,
            column=6,
            value=float(amt) if amt is not None and isinstance(amt, (int, float, Decimal)) else amt,
        )
        audit_sheet.cell(row=audit_row, column=7, value=t.get("schedule"))
        audit_sheet.cell(row=audit_row, column=8, value=t.get("confidence"))
        audit_sheet.cell(row=audit_row, column=9, value=t.get("schedule"))
        audit_sheet.cell(row=audit_row, column=10, value=bool(t.get("edited_by_staff")))
        audit_sheet.cell(
            row=audit_row, column=11, value=verifier_email or t.get("verified_by")
        )
        va = t.get("verified_at")
        audit_sheet.cell(
            row=audit_row,
            column=12,
            value=va.isoformat() if hasattr(va, "isoformat") else va,
        )
        audit_row += 1

    audit_sheet.sheet_state = "hidden"

    out_name = (
        f"{_safe_filename(matter_name)}_Accounting_{period_start}_to_{period_end}_{date.today()}.xlsx"
    )
    out_path = Path("data") / "exports" / out_name
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path


def _safe_filename(s: str) -> str:
    out = "".join(c if c.isalnum() or c in "._- " else "_" for c in s)
    return out.strip()[:80] or "Matter"
