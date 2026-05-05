"""Populate firm Excel template and append hidden audit trail sheet — legacy + spec v1.2."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.utils import column_index_from_string
from app.config import get_settings, master_template_path
from app.schedules import AD_HOC_SCHEDULE_LETTERS, ALL_SCHEDULE_LETTERS
from app.template_config import (
    MasterTemplateMappingV12,
    TemplateMappingFile,
    load_mapping_any,
    schedules_for_matter,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve_path(p: Path) -> Path:
    return p if p.is_absolute() else (_REPO_ROOT / p).resolve()


def _col(letter: str) -> int:
    return column_index_from_string(letter.strip().upper())


def _safe_filename(s: str) -> str:
    out = "".join(c if c.isalnum() or c in "._- " else "_" for c in s)
    return out.strip()[:80] or "Matter"


def parse_schedule_letter(raw: Optional[str]) -> Optional[str]:
    """Return schedule letter for Excel placement, or None if excluded / needs review / not mapped."""
    if not raw:
        return None
    s = str(raw).strip().upper()
    if s in ("INTERNAL_TRANSFER", "EXCLUDED", "NEEDS_REVIEW", "?", ""):
        return None
    if s in ALL_SCHEDULE_LETTERS:
        return s
    return None


def _write_row(
    ws,
    row: int,
    columns: dict[str, str],
    txn: dict[str, Any],
) -> None:
    if "date" in columns and txn.get("txn_date") is not None:
        d = txn["txn_date"]
        val = d.isoformat() if hasattr(d, "isoformat") else str(d)
        ws.cell(row=row, column=_col(columns["date"]), value=val)
    if "description" in columns:
        ws.cell(
            row=row,
            column=_col(columns["description"]),
            value=txn.get("description") or "",
        )
    if "amount" in columns and txn.get("amount") is not None:
        ws.cell(
            row=row,
            column=_col(columns["amount"]),
            value=float(txn["amount"]),
        )
    if "payee" in columns:
        ws.cell(
            row=row,
            column=_col(columns["payee"]),
            value=txn.get("payee") or "",
        )
    if "category" in columns:
        ws.cell(
            row=row,
            column=_col(columns["category"]),
            value=txn.get("subcategory") or "",
        )
    if "notes" in columns:
        ws.cell(
            row=row,
            column=_col(columns["notes"]),
            value=txn.get("notes") or "",
        )


def _generate_legacy(
    matter_type: str,
    matter_name: str,
    period_start: date,
    period_end: date,
    transactions: list[dict[str, Any]],
    statement_by_id: dict[str, dict[str, Any]],
    mapping: TemplateMappingFile,
    verifier_email: Optional[str],
) -> Path:
    template_path = _resolve_path(master_template_path())
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")

    schedule_map = schedules_for_matter(mapping, matter_type)
    wb = load_workbook(template_path)

    by_sched: dict[str, list[dict]] = {k: [] for k in schedule_map}
    for t in transactions:
        if t.get("excluded"):
            continue
        if t.get("internal_transfer"):
            continue
        letter = parse_schedule_letter(t.get("schedule"))
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

    _append_audit_legacy(
        wb,
        transactions,
        statement_by_id,
        written_row,
        verifier_email,
    )

    return _save_workbook(wb, matter_name, period_start, period_end)


def _append_audit_legacy(
    wb,
    transactions: list[dict[str, Any]],
    statement_by_id: dict[str, dict[str, Any]],
    written_row: dict[str, int],
    verifier_email: Optional[str],
) -> None:
    audit_sheet = wb.create_sheet("_AuditTrail")
    audit_headers = [
        "Schedule",
        "Subcategory",
        "RowInSchedule",
        "SourceFile",
        "SourcePage",
        "OriginalDescription",
        "NormalizedDescription",
        "Amount",
        "AIScheduleSuggestion",
        "AISubcategorySuggestion",
        "AIConfidence",
        "FinalSchedule",
        "FinalSubcategory",
        "EditedByStaff",
        "VerifiedBy",
        "VerificationTimestamp",
    ]
    for c, h in enumerate(audit_headers, start=1):
        audit_sheet.cell(row=1, column=c, value=h).font = Font(bold=True)

    audit_row = 2
    for t in sorted(
        transactions, key=lambda x: (x.get("statement_id") or "", x.get("id") or "")
    ):
        letter = parse_schedule_letter(t.get("schedule"))
        if t.get("internal_transfer") or t.get("excluded"):
            sched_display = (
                "internal_transfer" if t.get("internal_transfer") else "excluded"
            )
        else:
            sched_display = letter or (t.get("schedule") or "")
        tid = t.get("id")
        rin = written_row.get(tid, "")
        sid = t.get("statement_id")
        st = statement_by_id.get(sid, {})
        amt = t.get("amount")
        amt_val = (
            float(amt)
            if amt is not None and isinstance(amt, (int, float, Decimal))
            else amt
        )
        va = t.get("verified_at")
        row_vals = [
            sched_display,
            t.get("subcategory"),
            rin,
            st.get("original_filename", ""),
            t.get("source_page"),
            t.get("description", ""),
            t.get("normalized_description") or "",
            amt_val,
            t.get("schedule"),
            t.get("subcategory"),
            t.get("confidence"),
            t.get("schedule"),
            t.get("subcategory"),
            bool(t.get("edited_by_staff")),
            verifier_email or t.get("verified_by"),
            va.isoformat() if hasattr(va, "isoformat") else va,
        ]
        for c, val in enumerate(row_vals, start=1):
            audit_sheet.cell(row=audit_row, column=c, value=val)
        audit_row += 1
    audit_sheet.sheet_state = "hidden"


def _match_subcategory_key(
    label: Optional[str], keys: list[str]
) -> Optional[str]:
    if not label or not keys:
        return None
    L = label.strip().lower()
    for k in keys:
        if k.strip().lower() == L:
            return k
    for k in keys:
        if L in k.lower() or k.lower() in L:
            return k
    return keys[0] if keys else None


def _generate_v12(
    matter_name: str,
    period_start: date,
    period_end: date,
    transactions: list[dict[str, Any]],
    statement_by_id: dict[str, dict[str, Any]],
    statements_order: list[str],
    mapping: MasterTemplateMappingV12,
    verifier_email: Optional[str],
    session_meta: dict[str, Any],
) -> Path:
    settings = get_settings()
    raw_tpl = mapping.template_path or str(settings.template_path)
    template_path = _resolve_path(Path(raw_tpl))
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")

    wb = load_workbook(template_path)
    sheets_cfg = mapping.sheets

    wb_meta = sheets_cfg.get("workingBalance") or {}
    wb_sheet_n = (wb_meta.get("sheet") or "Working Balance").strip()
    if wb_sheet_n in wb.sheetnames:
        ws_wb = wb[wb_sheet_n]
        if c := wb_meta.get("matterNameCell"):
            ws_wb[c] = matter_name
        if c := wb_meta.get("caseNumberCell"):
            ws_wb[c] = session_meta.get("case_number") or ""
        if c := wb_meta.get("accountingTypeCell"):
            ws_wb[c] = session_meta.get("accounting_type") or ""
        if c := wb_meta.get("fiduciaryNameCell"):
            ws_wb[c] = session_meta.get("fiduciary_name") or ""
        if c := wb_meta.get("periodCell"):
            ws_wb[c] = f"{period_start} to {period_end}"

    bank_cfg = sheets_cfg.get("bankTransactions") or {}
    bank_sheet_n = (bank_cfg.get("sheet") or "Bank Statement Transactions").strip()
    bank_cols = bank_cfg.get("columns") or {
        "date": "A",
        "description": "B",
        "account": "C",
        "check": "D",
        "copy_chk": "E",
        "debit": "F",
        "credit": "G",
        "additional_info": "H",
    }
    first_row = int(bank_cfg.get("firstBlockStartRow", 5))
    gap = int(bank_cfg.get("blockGapRows", 2))

    written_row: dict[str, tuple[str, int]] = {}  # txn_id -> (sheet name, row)

    if bank_sheet_n in wb.sheetnames:
        ws_bank = wb[bank_sheet_n]
        row_ptr = first_row
        for stmt_id in statements_order:
            st = statement_by_id.get(stmt_id)
            if not st:
                continue
            stmt_tx = [t for t in transactions if t.get("statement_id") == stmt_id]
            header = " ".join(
                filter(
                    None,
                    [
                        st.get("institution") or "",
                        st.get("account_last4")
                        and f"…{st.get('account_last4')}",
                    ],
                )
            )
            ws_bank.cell(row=row_ptr, column=1, value=header or "Account")
            row_ptr += 1
            for t in stmt_tx:
                amt = t.get("amount")
                debit_val = None
                credit_val = None
                if amt is not None:
                    try:
                        v = float(amt)
                        if v < 0:
                            debit_val = -v
                        else:
                            credit_val = v
                    except (TypeError, ValueError):
                        pass
                if "date" in bank_cols and t.get("txn_date") is not None:
                    d = t["txn_date"]
                    ws_bank.cell(
                        row=row_ptr,
                        column=_col(bank_cols["date"]),
                        value=d.isoformat() if hasattr(d, "isoformat") else d,
                    )
                if "description" in bank_cols:
                    ws_bank.cell(
                        row=row_ptr,
                        column=_col(bank_cols["description"]),
                        value=t.get("description") or "",
                    )
                if "account" in bank_cols:
                    ws_bank.cell(
                        row=row_ptr,
                        column=_col(bank_cols["account"]),
                        value=st.get("account_last4") or "",
                    )
                if "check" in bank_cols:
                    ws_bank.cell(row=row_ptr, column=_col(bank_cols["check"]), value="")
                if "copy_chk" in bank_cols:
                    ws_bank.cell(row=row_ptr, column=_col(bank_cols["copy_chk"]), value="")
                if "debit" in bank_cols and debit_val is not None:
                    ws_bank.cell(
                        row=row_ptr, column=_col(bank_cols["debit"]), value=debit_val
                    )
                if "credit" in bank_cols and credit_val is not None:
                    ws_bank.cell(
                        row=row_ptr, column=_col(bank_cols["credit"]), value=credit_val
                    )
                if "additional_info" in bank_cols:
                    ws_bank.cell(
                        row=row_ptr,
                        column=_col(bank_cols["additional_info"]),
                        value=t.get("notes") or "",
                    )
                tid = t.get("id")
                if tid:
                    written_row[tid] = (bank_sheet_n, row_ptr)
                row_ptr += 1
            row_ptr += gap

    schedule_a = sheets_cfg.get("scheduleA") or {}
    if schedule_a:
        sheet_a_name = (schedule_a.get("sheet") or "Schedule A").strip()
        subs = schedule_a.get("subcategories") or {}
        sub_keys = list(subs.keys())
        cursors: dict[str, int] = {}
        for sk, meta in subs.items():
            if isinstance(meta, dict) and "startRow" in meta:
                cursors[sk] = int(meta["startRow"])

        def _sheet_name_match(name: str) -> Optional[str]:
            for sn in wb.sheetnames:
                if sn.strip().lower() == name.strip().lower():
                    return sn
            return None

        actual_a = _sheet_name_match(sheet_a_name)
        if actual_a:
            ws_a = wb[actual_a]
            for t in transactions:
                if t.get("excluded") or t.get("internal_transfer"):
                    continue
                if parse_schedule_letter(t.get("schedule")) != "A":
                    continue
                sk = _match_subcategory_key(t.get("subcategory"), sub_keys)
                if not sk or sk not in cursors:
                    continue
                r = cursors[sk]
                cols = schedule_a.get("columns") or {
                    "date": "A",
                    "description": "B",
                    "amount": "C",
                }
                if "date" in cols and t.get("txn_date") is not None:
                    d = t["txn_date"]
                    ws_a.cell(
                        row=r,
                        column=_col(cols["date"]),
                        value=d.isoformat() if hasattr(d, "isoformat") else d,
                    )
                if "description" in cols:
                    ws_a.cell(
                        row=r,
                        column=_col(cols["description"]),
                        value=t.get("description") or "",
                    )
                if "amount" in cols and t.get("amount") is not None:
                    ws_a.cell(
                        row=r, column=_col(cols["amount"]), value=float(t["amount"])
                    )
                tid = t.get("id")
                if tid:
                    written_row[tid] = (actual_a, r)
                cursors[sk] = r + 1

    schedule_c = sheets_cfg.get("scheduleC") or {}
    if schedule_c:
        sheet_c_name = (schedule_c.get("sheet") or "Schedule C").strip()
        subs_c = schedule_c.get("subcategories") or {}
        sub_keys_c = list(subs_c.keys())
        cursors_c: dict[str, int] = {}
        for sk, meta in subs_c.items():
            if isinstance(meta, dict) and "startRow" in meta:
                cursors_c[sk] = int(meta["startRow"])

        def _find_c(name: str) -> Optional[str]:
            for sn in wb.sheetnames:
                if sn.strip().lower() == name.strip().lower():
                    return sn
            return None

        actual_c = _find_c(sheet_c_name)
        if actual_c:
            ws_c = wb[actual_c]
            cols_c = schedule_c.get("columns") or {
                "date": "A",
                "account": "B",
                "description": "C",
                "check": "D",
                "amount": "E",
            }
            for t in transactions:
                if t.get("excluded") or t.get("internal_transfer"):
                    continue
                if parse_schedule_letter(t.get("schedule")) != "C":
                    continue
                sk = _match_subcategory_key(t.get("subcategory"), sub_keys_c)
                if not sk or sk not in cursors_c:
                    continue
                r = cursors_c[sk]
                st = statement_by_id.get(t.get("statement_id") or "", {})
                if "date" in cols_c and t.get("txn_date") is not None:
                    d = t["txn_date"]
                    ws_c.cell(
                        row=r,
                        column=_col(cols_c["date"]),
                        value=d.isoformat() if hasattr(d, "isoformat") else d,
                    )
                if "account" in cols_c:
                    ws_c.cell(
                        row=r,
                        column=_col(cols_c["account"]),
                        value=st.get("account_last4") or "",
                    )
                if "description" in cols_c:
                    ws_c.cell(
                        row=r,
                        column=_col(cols_c["description"]),
                        value=t.get("description") or "",
                    )
                if "check" in cols_c:
                    ws_c.cell(row=r, column=_col(cols_c["check"]), value="")
                if "amount" in cols_c and t.get("amount") is not None:
                    ws_c.cell(
                        row=r, column=_col(cols_c["amount"]), value=float(t["amount"])
                    )
                tid = t.get("id")
                if tid:
                    written_row[tid] = (actual_c, r)
                cursors_c[sk] = r + 1

    schedule_f = sheets_cfg.get("scheduleF") or {}
    if schedule_f:
        sheet_f_name = (schedule_f.get("sheet") or "Schedule F").strip()
        data_start = int(schedule_f.get("dataStartRow", 7))
        cols_f = schedule_f.get("columns") or {
            "date": "A",
            "description": "B",
            "carry_value": "C",
        }

        def _find_f(name: str) -> Optional[str]:
            for sn in wb.sheetnames:
                if sn.strip().lower() == name.strip().lower():
                    return sn
            return None

        actual_f = _find_f(sheet_f_name)
        if actual_f:
            ws_f = wb[actual_f]
            r = data_start
            for t in transactions:
                if t.get("excluded") or t.get("internal_transfer"):
                    continue
                if parse_schedule_letter(t.get("schedule")) != "F":
                    continue
                if "date" in cols_f and t.get("txn_date") is not None:
                    d = t["txn_date"]
                    ws_f.cell(
                        row=r,
                        column=_col(cols_f["date"]),
                        value=d.isoformat() if hasattr(d, "isoformat") else d,
                    )
                if "description" in cols_f:
                    ws_f.cell(
                        row=r,
                        column=_col(cols_f["description"]),
                        value=t.get("description") or "",
                    )
                key_cv = "carry_value" if "carry_value" in cols_f else "amount"
                if key_cv in cols_f and t.get("amount") is not None:
                    ws_f.cell(
                        row=r,
                        column=_col(cols_f[key_cv]),
                        value=float(t["amount"]),
                    )
                tid = t.get("id")
                if tid:
                    written_row[tid] = (actual_f, r)
                r += 1

    for letter in AD_HOC_SCHEDULE_LETTERS:
        ad_tx = []
        for t in transactions:
            if t.get("excluded") or t.get("internal_transfer"):
                continue
            if parse_schedule_letter(t.get("schedule")) == letter:
                ad_tx.append(t)
        if not ad_tx:
            continue
        meta = mapping.ad_hoc_schedules.get(letter)
        label = meta.label if meta else f"Schedule {letter}"
        sheet_title = f"Schedule {letter}"
        if sheet_title in wb.sheetnames:
            ws_ad = wb[sheet_title]
        else:
            ws_ad = wb.create_sheet(sheet_title)
            ws_ad["A1"] = label
            ws_ad["A2"] = "Date"
            ws_ad["B2"] = "Description"
            ws_ad["C2"] = "Amount"
            ws_ad["D2"] = "Subcategory"
        r = 3
        for t in ad_tx:
            if t.get("txn_date") is not None:
                d = t["txn_date"]
                ws_ad.cell(
                    row=r,
                    column=1,
                    value=d.isoformat() if hasattr(d, "isoformat") else d,
                )
            ws_ad.cell(row=r, column=2, value=t.get("description") or "")
            if t.get("amount") is not None:
                ws_ad.cell(row=r, column=3, value=float(t["amount"]))
            ws_ad.cell(row=r, column=4, value=t.get("subcategory") or "")
            tid = t.get("id")
            if tid:
                written_row[tid] = (sheet_title, r)
            r += 1

    _append_audit_v12(
        wb,
        transactions,
        statement_by_id,
        written_row,
        verifier_email,
    )

    return _save_workbook(wb, matter_name, period_start, period_end)


def _append_audit_v12(
    wb,
    transactions: list[dict[str, Any]],
    statement_by_id: dict[str, dict[str, Any]],
    written_row: dict[str, tuple[str, int]],
    verifier_email: Optional[str],
) -> None:
    audit_sheet = wb.create_sheet("_AuditTrail")
    headers = [
        "Schedule",
        "Subcategory",
        "RowInScheduleSheet",
        "SourceFile",
        "SourcePage",
        "OriginalDescription",
        "NormalizedDescription",
        "Amount",
        "AIScheduleSuggestion",
        "AISubcategorySuggestion",
        "AIConfidence",
        "FinalSchedule",
        "FinalSubcategory",
        "EditedByStaff",
        "VerifiedBy",
        "VerificationTimestamp",
    ]
    for c, h in enumerate(headers, start=1):
        audit_sheet.cell(row=1, column=c, value=h).font = Font(bold=True)

    row_i = 2
    for t in sorted(
        transactions, key=lambda x: (x.get("statement_id") or "", x.get("id") or "")
    ):
        letter = parse_schedule_letter(t.get("schedule"))
        if t.get("internal_transfer") or t.get("excluded"):
            sched_display = (
                "internal_transfer" if t.get("internal_transfer") else "excluded"
            )
        else:
            sched_display = letter or (t.get("schedule") or "")
        tid = t.get("id")
        loc = written_row.get(tid)
        rin = loc[1] if loc else ""
        sid = t.get("statement_id")
        st = statement_by_id.get(sid, {})
        amt = t.get("amount")
        amt_val = (
            float(amt)
            if amt is not None and isinstance(amt, (int, float, Decimal))
            else amt
        )
        va = t.get("verified_at")
        vals = [
            sched_display,
            t.get("subcategory"),
            rin,
            st.get("original_filename", ""),
            t.get("source_page"),
            t.get("description", ""),
            t.get("normalized_description") or "",
            amt_val,
            t.get("schedule"),
            t.get("subcategory"),
            t.get("confidence"),
            t.get("schedule"),
            t.get("subcategory"),
            bool(t.get("edited_by_staff")),
            verifier_email or t.get("verified_by"),
            va.isoformat() if hasattr(va, "isoformat") else va,
        ]
        for c, val in enumerate(vals, start=1):
            audit_sheet.cell(row=row_i, column=c, value=val)
        row_i += 1
    audit_sheet.sheet_state = "hidden"


def _save_workbook(
    wb: Workbook, matter_name: str, period_start: date, period_end: date
) -> Path:
    out_name = f"{_safe_filename(matter_name)}_Accounting_{period_start}_to_{period_end}_{date.today()}.xlsx"
    out_path = _REPO_ROOT / "data" / "exports" / out_name
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path


def generate_accounting_workbook(
    matter_type: str,
    matter_name: str,
    period_start: date,
    period_end: date,
    transactions: list[dict[str, Any]],
    statement_by_id: dict[str, dict[str, Any]],
    mapping_path: Path,
    verifier_email: Optional[str],
    statement_order: Optional[list[str]] = None,
    session_meta: Optional[dict[str, Any]] = None,
) -> Path:
    """
    Generate Excel export. Uses spec v1.2 mapping when JSON contains `sheets.workingBalance`;
    otherwise legacy flat schedule→sheet mapping.
    """
    meta = session_meta or {}
    mapping = load_mapping_any(mapping_path)
    stmt_order = statement_order or list(statement_by_id.keys())

    if isinstance(mapping, MasterTemplateMappingV12):
        return _generate_v12(
            matter_name=matter_name,
            period_start=period_start,
            period_end=period_end,
            transactions=transactions,
            statement_by_id=statement_by_id,
            statements_order=stmt_order,
            mapping=mapping,
            verifier_email=verifier_email,
            session_meta=meta,
        )

    return _generate_legacy(
        matter_type=matter_type,
        matter_name=matter_name,
        period_start=period_start,
        period_end=period_end,
        transactions=transactions,
        statement_by_id=statement_by_id,
        mapping=mapping,
        verifier_email=verifier_email,
    )
