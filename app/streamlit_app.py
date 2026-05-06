"""Golden Oaks Probate Accounting — Streamlit entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path

# Streamlit puts the script's directory on sys.path, not the project root, so `import app` fails in Docker/Coolify unless PYTHONPATH is set. Ensure repo root is importable.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import base64
import csv
import hashlib
import io
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import streamlit as st
import streamlit.components.v1 as components

from app import bootstrap_templates
from app.admin_settings_store import is_admin_user, load_admin_settings
from app.debug_agent_log import agent_debug_log
from app.auth_entra import get_auth_url, handle_oauth_callback, require_user
from app.config import get_settings
from app.db import (
    AccountingSessionORM,
    StatementORM,
    TransactionORM,
    new_id,
    session_scope,
)
from app.excel_writer import generate_accounting_workbook
from app.gemini_categorizer import categorize_with_gemini
from app.gemini_description_cleanup import cleanup_descriptions_with_gemini
from app.gemini_extractor import extract_statement_with_gemini
from app.models import ExtractionResult
from app.pdf_ingest import PdfValidationError, save_uploaded_pdf, validate_pdf
from app.reconciler import apply_internal_transfer_flags, issues_to_json, run_reconciliation
from app.schedules import SCHEDULE_UI_OPTIONS
from app.text_extract import extract_pdf


st.set_page_config(
    page_title="Golden Oaks | Probate Accounting",
    page_icon="📑",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _inject_workbench_layout_css() -> None:
    """Apply Golden Oaks brand tokens and reclaim horizontal workspace."""
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Open+Sans:wght@400;600&family=Poppins:wght@600;700&display=swap');

        :root {
            --color-burgundy: #8C2A2A;
            --color-burgundy-dark: #6E2020;
            --color-gold: #C49B3B;
            --color-gold-dark: #A8822D;
            --color-gold-tint: #F0E2B8;
            --color-ink: #141F2E;
            --color-body: #3D3D3D;
            --color-bg: #FFFFFF;
            --color-bg-soft: #F5F5F3;
            --color-border: #D8D8D5;
            --radius-pill: 999px;
            --radius-card: 8px;
        }

        html, body, [class*="css"] {
            font-family: "Open Sans", system-ui, -apple-system, sans-serif;
            color: var(--color-body);
        }

        .stApp {
            background: var(--color-bg);
        }

        p, label, span, div {
            color: var(--color-body);
        }

        h1, h2, h3, h4, h5, h6,
        [data-testid="stMarkdownContainer"] h1,
        [data-testid="stMarkdownContainer"] h2,
        [data-testid="stMarkdownContainer"] h3 {
            font-family: "Poppins", system-ui, -apple-system, sans-serif;
            color: var(--color-ink);
            font-weight: 700;
            letter-spacing: 0.1px;
        }

        [data-testid="stSidebar"] {
            width: 16rem !important;
            min-width: 16rem !important;
            max-width: 16rem !important;
            background: var(--color-bg-soft);
            border-right: 1px solid var(--color-border);
        }
        [data-testid="stSidebar"] {
            width: 16rem !important;
            min-width: 16rem !important;
            max-width: 16rem !important;
        }
        [data-testid="stSidebar"] > div:first-child {
            width: 16rem !important;
        }
        .main .block-container {
            padding-top: 1.25rem;
            padding-left: 2.25rem;
            padding-right: 2.25rem;
            padding-bottom: 1rem;
        }

        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"],
        [data-testid="stSidebar"] [data-testid="stText"] {
            color: var(--color-ink);
        }

        .stTabs [data-baseweb="tab-list"] {
            gap: 0.25rem;
            border-bottom: 2px solid var(--color-border);
        }
        .stTabs [data-baseweb="tab"] {
            font-family: "Poppins", system-ui, -apple-system, sans-serif;
            color: var(--color-ink);
            font-weight: 600;
            border-bottom: 2px solid transparent;
        }
        .stTabs [aria-selected="true"] {
            color: var(--color-burgundy);
            border-bottom-color: var(--color-gold);
        }

        .stButton > button,
        .stDownloadButton > button,
        .stLinkButton > a {
            background: var(--color-gold);
            color: #FFFFFF;
            border: 1px solid var(--color-gold);
            border-radius: var(--radius-pill);
            font-family: "Poppins", system-ui, -apple-system, sans-serif;
            font-weight: 600;
            padding: 0.4rem 1rem;
            transition: background-color 0.15s ease, border-color 0.15s ease;
        }
        .stButton > button:hover,
        .stDownloadButton > button:hover,
        .stLinkButton > a:hover {
            background: var(--color-gold-dark);
            border-color: var(--color-gold-dark);
            color: #FFFFFF;
        }

        .stTextInput input,
        .stTextArea textarea,
        .stDateInput input {
            background: #FFFFFF !important;
            color: var(--color-ink) !important;
            border: 1px solid var(--color-border) !important;
        }

        .stSelectbox [data-baseweb="select"] > div,
        .stMultiSelect [data-baseweb="select"] > div {
            background: #FFFFFF !important;
            color: var(--color-ink) !important;
            border: 1px solid var(--color-border) !important;
        }
        .stSelectbox [data-baseweb="select"] * ,
        .stMultiSelect [data-baseweb="select"] * {
            color: var(--color-ink) !important;
        }

        .stTextInput input:focus,
        .stTextArea textarea:focus,
        .stDateInput input:focus {
            border-color: var(--color-burgundy) !important;
            box-shadow: 0 0 0 1px var(--color-burgundy) !important;
        }

        [data-baseweb="input"] input,
        [data-baseweb="textarea"] textarea {
            background: #FFFFFF !important;
            color: var(--color-ink) !important;
        }

        .stFileUploader [data-testid="stFileUploaderDropzone"] {
            background: var(--color-bg-soft);
            border: 1px dashed var(--color-border);
        }
        .stFileUploader [data-testid="stFileUploaderDropzone"] * {
            color: var(--color-ink) !important;
        }

        [data-testid="stMarkdownContainer"] a {
            color: var(--color-burgundy);
        }

        [data-testid="stMetricValue"] {
            color: var(--color-burgundy);
            font-family: "Poppins", system-ui, -apple-system, sans-serif;
            font-weight: 700;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _split_pane_scroll_height(row_count: int, cap: int = 680) -> int:
    """Match PDF viewer and data editor heights in two-column review layouts."""
    return min(cap, 88 + 26 * max(row_count, 1))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Session workflow: per-statement extraction + categorization; reconciliation once all are done
ST_DRAFT = "draft"
ST_PROCESSING = "processing"
ST_RECONCILIATION_COMPLETE = "reconciliation_complete"
ST_PENDING_REVIEW = "pending_review"
ST_COMPLETED = "completed"


def migrate_workflow_status(sid: str) -> None:
    """Normalize legacy session statuses; backfill per-statement flags for already-finished sessions."""
    with session_scope() as db:
        row = db.get(AccountingSessionORM, sid)
        if not row:
            return
        if row.status in (
            "pending_categorization",
            "extraction_complete",
            "extraction_approved",
            "categorization_complete",
            "categorization_approved",
        ):
            row.status = ST_PROCESSING
        stmts = db.query(StatementORM).filter_by(session_id=sid).all()
        if row.status in (ST_RECONCILIATION_COMPLETE, ST_PENDING_REVIEW, ST_COMPLETED):
            for s in stmts:
                if s.pdf_storage_path:
                    s.extraction_human_approved = True
                    s.categorization_ai_done = True
                    s.categorization_human_approved = True


def _statements_with_pdf(stmts: list[StatementORM]) -> list[StatementORM]:
    return [s for s in stmts if s.pdf_storage_path]


def _effective_description_for_category(t: TransactionORM) -> Optional[str]:
    if (t.normalized_description or "").strip():
        return (t.normalized_description or "").strip()
    if (t.description_ai_cleaned or "").strip():
        return (t.description_ai_cleaned or "").strip()
    return (t.description or "").strip() or None


def _extraction_description_resolved(t: TransactionORM) -> bool:
    conf = (t.description_cleanup_confidence or "").lower()
    if conf == "high":
        return True
    if t.description_staff_accepted:
        return True
    if t.client_clarification:
        return True
    return False


def _effective_payee(t: TransactionORM) -> Optional[str]:
    if (t.payee_normalized or "").strip():
        return (t.payee_normalized or "").strip()
    if (t.payee or "").strip():
        return (t.payee or "").strip()
    return None


def _extraction_payee_resolved(t: TransactionORM) -> bool:
    if t.client_clarification:
        return True
    if not (t.payee or "").strip() and not (t.payee_normalized or "").strip():
        return True
    return bool(t.payee_staff_accepted)


def _statement_descriptions_resolved(stmt_tx: list[TransactionORM]) -> bool:
    if not stmt_tx:
        return True
    return all(_extraction_description_resolved(t) for t in stmt_tx)


def _statement_extraction_review_resolved(stmt_tx: list[TransactionORM]) -> bool:
    if not stmt_tx:
        return True
    return all(
        _extraction_description_resolved(t) and _extraction_payee_resolved(t)
        for t in stmt_tx
    )


def _apply_description_cleanup_to_rows(
    stt: StatementORM, rows: list[TransactionORM]
) -> None:
    if not rows:
        return
    payload = [
        {
            "transactionId": r.id,
            "description": r.description,
            "institution": stt.institution,
            "accountLast4": stt.account_last4,
        }
        for r in rows
    ]
    cres = cleanup_descriptions_with_gemini(payload)
    cmap = {c.transaction_id: c for c in cres.cleanups}
    for r in rows:
        c = cmap.get(r.id)
        if not c:
            continue
        r.description_ai_cleaned = c.cleaned_description
        r.description_cleanup_confidence = (
            c.confidence.value if c.confidence else "medium"
        )
        r.description_cleanup_reasoning = c.reasoning


def run_description_cleanup_for_statement(session_id: str, statement_id: str) -> None:
    settings = get_settings()
    if not settings.gemini_api_key:
        st.error("Configure GEMINI_API_KEY to run description cleanup.")
        return
    with session_scope() as db:
        stt = db.get(StatementORM, statement_id)
        if not stt or stt.session_id != session_id:
            return
        if stt.extraction_status != "extracted":
            st.error("Extract this statement before running description cleanup.")
            return
        rows = db.query(TransactionORM).filter_by(statement_id=statement_id).all()
        if not rows:
            st.info("No transactions to clean.")
            return
        try:
            _apply_description_cleanup_to_rows(stt, rows)
        except Exception as e:
            st.error(f"Description cleanup failed: {e}")
            return
        sess = db.get(AccountingSessionORM, session_id)
        if sess and sess.status in (ST_RECONCILIATION_COMPLETE, ST_PENDING_REVIEW):
            sess.status = ST_PROCESSING


def all_statements_ready_for_reconciliation(stmts: list[StatementORM]) -> bool:
    """Every uploaded PDF statement must be extracted, human-approved, categorized, and cat-approved."""
    need = _statements_with_pdf(stmts)
    if not need:
        return False
    for s in need:
        if s.extraction_status != "extracted":
            return False
        if not getattr(s, "extraction_human_approved", False):
            return False
        if not getattr(s, "categorization_ai_done", False):
            return False
        if not getattr(s, "categorization_human_approved", False):
            return False
    return True


def run_extraction_for_statement(session_id: str, statement_id: str) -> None:
    settings = get_settings()
    if not settings.gemini_api_key:
        st.error("Configure GEMINI_API_KEY to run extraction.")
        return
    with session_scope() as db:
        sess = db.get(AccountingSessionORM, session_id)
        stt = db.get(StatementORM, statement_id)
        if not sess or not stt or stt.session_id != session_id:
            return
        sess.reconciliation_json = None
        if sess.status in (ST_RECONCILIATION_COMPLETE, ST_PENDING_REVIEW):
            sess.status = ST_PROCESSING
        stt.extraction_human_approved = False
        stt.categorization_ai_done = False
        stt.categorization_human_approved = False

        if not stt.pdf_storage_path or not Path(stt.pdf_storage_path).exists():
            stt.extraction_status = "error"
            sess.status = ST_PROCESSING
            return
        try:
            doc = extract_pdf(Path(stt.pdf_storage_path))
            result: ExtractionResult = extract_statement_with_gemini(
                doc.combined_text, doc.tables_summary
            )
            stt.institution = result.institution
            stt.account_type = result.account_type
            stt.account_last4 = result.account_number_last4
            stt.statement_period_start = result.statement_period_start
            stt.statement_period_end = result.statement_period_end
            stt.beginning_balance = result.beginning_balance
            stt.ending_balance = result.ending_balance
            stt.extraction_status = "extracted"
            if result.flags:
                stt.extraction_flags_json = json.dumps(result.flags)

            db.query(TransactionORM).filter_by(statement_id=stt.id).delete()
            new_rows: list[TransactionORM] = []
            for ex in result.transactions:
                ta = ex.model_dump()
                tid = new_id()
                row = TransactionORM(
                    id=tid,
                    statement_id=stt.id,
                    txn_date=ta.get("date"),
                    description=ta.get("description"),
                    payee=ta.get("payee"),
                    amount=ta.get("amount"),
                    txn_type=ta.get("txn_type"),
                    balance_after=ta.get("balance"),
                    source_page=ta.get("source_page"),
                    security_symbol=ta.get("security_symbol"),
                    quantity=ta.get("quantity"),
                    price=ta.get("price"),
                    cost_basis=ta.get("cost_basis"),
                )
                db.add(row)
                new_rows.append(row)
            if new_rows and settings.gemini_api_key:
                try:
                    _apply_description_cleanup_to_rows(stt, new_rows)
                    for r in new_rows:
                        r.description_staff_accepted = False
                        r.client_clarification = False
                        r.payee_staff_accepted = False
                except Exception as e:
                    st.warning(
                        f"**{stt.original_filename}**: description cleanup failed "
                        f"(transactions still saved): {e}"
                    )
            if not result.transactions:
                st.warning(
                    f"**{stt.original_filename}**: extraction returned **0 transactions**. "
                    "The PDF may be image-only (try a clearer scan), or table detection failed."
                )
        except Exception as e:
            stt.extraction_status = f"error: {e}"
        sess.status = ST_PROCESSING


def run_categorization_for_statement(session_id: str, statement_id: str) -> None:
    settings = get_settings()
    if not settings.gemini_api_key:
        st.error("Configure GEMINI_API_KEY to run categorization.")
        return
    with session_scope() as db:
        sess = db.get(AccountingSessionORM, session_id)
        stt = db.get(StatementORM, statement_id)
        if not sess or not stt or stt.session_id != session_id:
            return
        if not stt.extraction_human_approved:
            st.error("Approve extraction for this statement before categorization.")
            return
        stmts = db.query(StatementORM).filter_by(session_id=session_id).all()
        txns = db.query(TransactionORM).filter_by(statement_id=statement_id).all()
        stt.categorization_human_approved = False
        if not txns:
            stt.categorization_ai_done = True
            sess.status = ST_PROCESSING
            return
        payload = []
        for t in txns:
            payload.append(
                {
                    "transactionId": t.id,
                    "date": t.txn_date.isoformat() if t.txn_date else None,
                    "description": _effective_description_for_category(t),
                    "payee": _effective_payee(t),
                    "amount": str(t.amount) if t.amount is not None else None,
                    "type": t.txn_type,
                    "institution": next(
                        (s.institution for s in stmts if s.id == t.statement_id), None
                    ),
                    "accountLast4": next(
                        (s.account_last4 for s in stmts if s.id == t.statement_id), None
                    ),
                }
            )
        try:
            cat = categorize_with_gemini(sess.matter_type, payload)
        except Exception as e:
            st.error(f"Categorization failed: {e}")
            return
        cmap = {c.transaction_id: c for c in cat.categorizations}
        for t in txns:
            c = cmap.get(t.id)
            if not c:
                continue
            t.schedule = c.schedule if isinstance(c.schedule, str) else str(c.schedule)
            t.subcategory = c.subcategory
            t.confidence = c.confidence.value if c.confidence else "medium"
            t.ai_reasoning = c.reasoning
        stt.categorization_ai_done = True
        sess.status = ST_PROCESSING


def run_reconciliation_stage(session_id: str) -> None:
    with session_scope() as db:
        sess = db.get(AccountingSessionORM, session_id)
        if not sess:
            return
        stmts = db.query(StatementORM).filter_by(session_id=session_id).all()
        if not all_statements_ready_for_reconciliation(stmts):
            return
        txns = (
            db.query(TransactionORM)
            .join(StatementORM)
            .filter(StatementORM.session_id == session_id)
            .all()
        )
        sdicts = [_statement_to_dict(s) for s in stmts]
        tdicts = [_transaction_to_dict(t) for t in txns]
        issues = run_reconciliation(sdicts, tdicts)
        apply_internal_transfer_flags(tdicts, issues)
        for t in txns:
            d = next((x for x in tdicts if x["id"] == t.id), None)
            if d:
                t.internal_transfer = bool(d.get("internal_transfer"))
                if d.get("internal_transfer"):
                    t.schedule = "internal_transfer"
        sess.reconciliation_json = issues_to_json(issues)
        sess.status = ST_RECONCILIATION_COMPLETE


def _transaction_to_dict(t: TransactionORM) -> dict[str, Any]:
    return {
        "id": t.id,
        "statement_id": t.statement_id,
        "txn_date": t.txn_date,
        "description": t.description,
        "amount": t.amount,
        "txn_type": t.txn_type,
        "balance_after": t.balance_after,
        "source_page": t.source_page,
        "schedule": t.schedule,
        "subcategory": t.subcategory,
        "confidence": t.confidence,
        "ai_reasoning": t.ai_reasoning,
        "notes": t.notes,
        "verified": t.verified,
        "excluded": t.excluded,
        "internal_transfer": t.internal_transfer,
        "edited_by_staff": t.edited_by_staff,
        "verified_at": t.verified_at,
        "verified_by": t.verified_by,
        "normalized_description": t.normalized_description,
        "payee": _effective_payee(t),
        "payee_raw": t.payee,
        "payee_normalized": t.payee_normalized,
        "payee_staff_accepted": t.payee_staff_accepted,
        "description_ai_cleaned": t.description_ai_cleaned,
        "description_cleanup_confidence": t.description_cleanup_confidence,
        "description_cleanup_reasoning": t.description_cleanup_reasoning,
        "description_staff_accepted": t.description_staff_accepted,
        "client_clarification": t.client_clarification,
    }


def _statement_to_dict(s: StatementORM) -> dict[str, Any]:
    return {
        "id": s.id,
        "original_filename": s.original_filename,
        "institution": s.institution,
        "account_last4": s.account_last4,
        "account_type": s.account_type,
        "statement_period_start": s.statement_period_start,
        "statement_period_end": s.statement_period_end,
        "beginning_balance": s.beginning_balance,
        "ending_balance": s.ending_balance,
        "extraction_status": s.extraction_status,
        "pdf_storage_path": s.pdf_storage_path,
    }


def load_full_session(sid: str) -> tuple[AccountingSessionORM, list[StatementORM], list[TransactionORM]]:
    with session_scope() as db:
        sess = db.get(AccountingSessionORM, sid)
        if not sess:
            raise ValueError("Session not found")
        stmts = (
            db.query(StatementORM)
            .filter_by(session_id=sid)
            .order_by(StatementORM.sort_order, StatementORM.id)
            .all()
        )
        txn_ids = [s.id for s in stmts]
        txns = []
        if txn_ids:
            txns = (
                db.query(TransactionORM)
                .filter(TransactionORM.statement_id.in_(txn_ids))
                .order_by(TransactionORM.txn_date, TransactionORM.id)
                .all()
            )
        return sess, stmts, txns


def ensure_session_in_state() -> None:
    if "current_session_id" not in st.session_state:
        st.session_state.current_session_id = None


def _extraction_review_table_rows(stmt_tx: list[TransactionORM]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for t in stmt_tx:
        raw = (t.description or "").strip()
        ai = (t.description_ai_cleaned or "").strip()
        conf = ((t.description_cleanup_confidence or "—").strip() or "—")
        default_clean = (t.normalized_description or "").strip() or ai or raw
        ext_payee = (t.payee or "").strip()
        payee_review = (t.payee_normalized or "").strip() or ext_payee
        rows.append(
            {
                "Date": t.txn_date.isoformat() if t.txn_date else "",
                "Extracted payee": ext_payee if len(ext_payee) <= 200 else ext_payee[:197] + "…",
                "Payee (review)": payee_review,
                "Raw description": raw if len(raw) <= 800 else raw[:797] + "…",
                "AI cleaned": ai if len(ai) <= 800 else ai[:797] + "…",
                "Cleanup conf": conf,
                "Clean description": default_clean,
                "Client clarification": bool(t.client_clarification),
            }
        )
    return rows


def _extraction_review_column_config(*, date_editable: bool) -> dict[str, Any]:
    return {
        "Date": st.column_config.TextColumn(
            "Date", disabled=not date_editable, width="small"
        ),
        "Extracted payee": st.column_config.TextColumn(
            "Extracted payee", disabled=True, width="small"
        ),
        "Payee (review)": st.column_config.TextColumn("Payee ✎", width="medium"),
        "Raw description": st.column_config.TextColumn(
            "Raw", disabled=True, width="medium"
        ),
        "AI cleaned": st.column_config.TextColumn(
            "AI clean", disabled=True, width="medium"
        ),
        "Cleanup conf": st.column_config.TextColumn(
            "Conf", disabled=True, width="small"
        ),
        "Clean description": st.column_config.TextColumn("Clean desc", width="large"),
        "Client clarification": st.column_config.CheckboxColumn(
            "Client?", width="small"
        ),
    }


def _persist_extraction_review_edits(
    stmt_tx: list[TransactionORM],
    ex_rows: list[dict[str, Any]],
    *,
    update_txn_date: bool = False,
) -> Optional[str]:
    if len(ex_rows) != len(stmt_tx):
        return "Row count mismatch — refresh and try again."
    with session_scope() as db:
        for orig, erow in zip(stmt_tx, ex_rows):
            row = db.get(TransactionORM, orig.id)
            if not row:
                continue
            if update_txn_date:
                ds = str(erow.get("Date") or "").strip()
                if ds:
                    try:
                        row.txn_date = date.fromisoformat(ds[:10])
                    except ValueError:
                        return f"Invalid date (use YYYY-MM-DD): {ds!r}"
                else:
                    row.txn_date = None
            clar = bool(erow.get("Client clarification"))
            clean = str(erow.get("Clean description") or "").strip()
            row.normalized_description = clean or None
            row.client_clarification = clar
            row.description_staff_accepted = not clar
            payee_rev = str(erow.get("Payee (review)") or "").strip()
            row.payee_normalized = payee_rev or None
            row.payee_staff_accepted = not clar
    return None


def _client_clarification_export_rows(
    txns: list[TransactionORM], stmts: list[StatementORM]
) -> list[dict[str, Any]]:
    by_id = {s.id: s for s in stmts}
    out: list[dict[str, Any]] = []
    for t in txns:
        if not t.client_clarification:
            continue
        stt = by_id.get(t.statement_id)
        out.append(
            {
                "Date": t.txn_date.isoformat() if t.txn_date else "",
                "Amount": float(t.amount) if t.amount is not None else None,
                "Extracted payee": (t.payee or "")[:500],
                "Payee (review)": (t.payee_normalized or "")[:500],
                "Raw description": (t.description or "")[:2000],
                "AI cleaned": (t.description_ai_cleaned or "")[:2000],
                "Cleanup confidence": t.description_cleanup_confidence or "",
                "Notes": (t.notes or "")[:2000],
                "Statement file": stt.original_filename if stt else "",
                "Account last4": stt.account_last4 if stt else "",
            }
        )
    return out


def render_pdf_html(pdf_path: Path, page: int = 1, *, height: int = 720) -> None:
    if not pdf_path.exists():
        # #region agent log
        agent_debug_log(
            "streamlit_app.py:render_pdf_html",
            "pdf_path_missing",
            {"path": str(pdf_path)},
            "H2",
        )
        # #endregion
        st.warning("PDF not found on disk.")
        return
    sz = pdf_path.stat().st_size
    est_b64 = (sz * 4 + 3) // 3
    # #region agent log
    agent_debug_log(
        "streamlit_app.py:render_pdf_html",
        "before_render",
        {
            "file_bytes": sz,
            "est_data_url_chars": est_b64,
            "page_hint": page,
            "likely_data_url_too_large": est_b64 > 1_500_000,
        },
        "H1",
    )
    # #endregion
    h = height
    key = "review_pdf_" + hashlib.sha256(
        str(pdf_path.resolve()).encode("utf-8", errors="replace")
    ).hexdigest()[:20]
    try:
        st.pdf(pdf_path, height=h, key=key)
        # #region agent log
        agent_debug_log(
            "streamlit_app.py:render_pdf_html",
            "render_used_st_pdf",
            {"key": key},
            "H1",
        )
        # #endregion
    except Exception as e:
        # #region agent log
        agent_debug_log(
            "streamlit_app.py:render_pdf_html",
            "st_pdf_failed_using_data_url_fallback",
            {"error_type": type(e).__name__, "error_message": str(e)[:500]},
            "H3",
        )
        # #endregion
        b64 = base64.b64encode(pdf_path.read_bytes()).decode("utf-8")
        components.html(
            f"""
        <iframe src="data:application/pdf;base64,{b64}#page={page}"
            width="100%" height="{h}" style="border:1px solid #ccc;"></iframe>
        """,
            height=h + 12,
        )


def main() -> None:
    bootstrap_templates.ensure_placeholder_templates()
    ensure_session_in_state()
    handle_oauth_callback()

    st.title("Golden Oaks | Probate Accounting")
    _inject_workbench_layout_css()
    user = require_user()
    settings = get_settings()

    if not user and not settings.skip_entra_auth:
        st.info("Sign in with your Microsoft work account to continue.")
        st.link_button("Sign in with Microsoft", get_auth_url())
        return

    if user:
        st.sidebar.caption(f"Signed in as {user}")
    if settings.skip_entra_auth:
        dev = st.sidebar.text_input("Dev user email", value=user or "dev@local.test")
        st.session_state["dev_user"] = dev

    # Sidebar — session list
    with session_scope() as db:
        sessions = (
            db.query(AccountingSessionORM)
            .order_by(AccountingSessionORM.created_at.desc())
            .limit(30)
            .all()
        )
    for s in sessions:
        label = f"{s.matter_name} ({s.status})"
        if st.sidebar.button(label, key=f"open_{s.id}"):
            st.session_state.current_session_id = s.id
    if st.sidebar.button("Start new accounting"):
        st.session_state.current_session_id = None
        st.session_state.pop("new_form", None)

    sid = st.session_state.current_session_id
    if not sid:
        st.subheader("New accounting session")
        with st.form("new_sess"):
            matter_name = st.text_input("Matter name / label")
            matter_id = st.text_input("Matter ID / ActionStep (optional)")
            matter_type = st.selectbox(
                "Matter type",
                ["probate_estate", "conservatorship", "trust_administration"],
            )
            accounting_type = st.selectbox(
                "Accounting type",
                ["First Account", "Subsequent Account"],
            )
            case_number = st.text_input("Court case number (optional)")
            fiduciary_name = st.text_input("Fiduciary name (optional)")
            c1, c2 = st.columns(2)
            with c1:
                p0 = st.date_input("Period start", value=date.today().replace(day=1))
            with c2:
                p1 = st.date_input("Period end", value=date.today())
            go = st.form_submit_button("Create session")
        if go and matter_name:
            nid = new_id()
            with session_scope() as db:
                db.add(
                    AccountingSessionORM(
                        id=nid,
                        matter_name=matter_name,
                        matter_id=matter_id or None,
                        matter_type=matter_type,
                        accounting_type=accounting_type,
                        case_number=case_number or None,
                        fiduciary_name=fiduciary_name or None,
                        period_start=p0,
                        period_end=p1,
                        status="draft",
                        owner_email=user,
                    )
                )
            st.session_state.current_session_id = nid
            st.rerun()
        return

    try:
        sess, stmts, txns = load_full_session(sid)
    except ValueError:
        st.error("Session not found.")
        return

    migrate_workflow_status(sid)
    sess, stmts, txns = load_full_session(sid)

    acct = getattr(sess, "accounting_type", None) or "—"
    st.caption(
        f"{sess.matter_name} | {sess.period_start} — {sess.period_end} | "
        f"Matter: {sess.matter_type} | Accounting: {acct}"
    )

    tab_upload, tab_extraction, tab_categorization, tab_finalize, tab_export = st.tabs(
        ["Upload", "Extraction", "Categorization & Review", "Finalize", "Export"]
    )

    with tab_upload:
        st.subheader("Upload statements (PDF)")
        files = st.file_uploader(
            "PDFs",
            type=["pdf"],
            accept_multiple_files=True,
        )
        if files:
            if len(files) > settings.max_session_files:
                st.error(f"Maximum {settings.max_session_files} files per session.")
            else:
                ok = True
                upload_root = settings.upload_dir / sid
                upload_root.mkdir(parents=True, exist_ok=True)
                for i, f in enumerate(files):
                    data = f.getvalue()
                    try:
                        tmp = save_uploaded_pdf(data, upload_root, f.name)
                        validate_pdf(tmp, settings.max_file_size_mb)
                        with session_scope() as db:
                            exists = (
                                db.query(StatementORM)
                                .filter_by(session_id=sid, original_filename=f.name)
                                .first()
                            )
                            if exists:
                                exists.pdf_storage_path = str(tmp)
                            else:
                                db.add(
                                    StatementORM(
                                        id=new_id(),
                                        session_id=sid,
                                        original_filename=f.name,
                                        pdf_storage_path=str(tmp),
                                        extraction_status="pending_extraction",
                                        sort_order=i,
                                    )
                                )
                    except PdfValidationError as e:
                        st.error(f"{f.name}: {e}")
                        ok = False
                if ok:
                    st.success("Upload complete.")
                    # Same script run: `stmts` was loaded before this commit; reload so other tabs see PDFs.
                    sess, stmts, txns = load_full_session(sid)
        if stmts:
            st.dataframe(
                [
                    {
                        "File": s.original_filename,
                        "Institution": s.institution,
                        "Account": s.account_last4,
                        "Status": s.extraction_status,
                    }
                    for s in stmts
                ]
            )

    with tab_extraction:
        st.subheader("Extraction and description review (per statement)")
        st.caption(
            "Run **AI extraction** (rows + automatic description cleanup). Review the PDF against rows, "
            "edit **Clean description** or mark **Client clarification**, save, then approve when every row "
            "is resolved (high-confidence AI, staff-reviewed, or sent to client)."
        )
        if not settings.gemini_api_key:
            st.warning(
                "Set GEMINI_API_KEY in the environment to enable AI extraction and description cleanup."
            )
        st.caption(
            f"**Session stage:** {sess.status.replace('_', ' ').title()} — "
            "open **Statements overview** below if you need the full list."
        )

        stmt_ids = [s.id for s in stmts if s.pdf_storage_path]
        if not stmt_ids:
            st.info("Upload at least one PDF on the **Upload** tab.")
        else:
            overview = []
            for s in _statements_with_pdf(stmts):
                n = sum(1 for t in txns if t.statement_id == s.id)
                overview.append(
                    {
                        "File": s.original_filename,
                        "AI extract": s.extraction_status,
                        "Extract ✓": "Yes" if s.extraction_human_approved else "—",
                        "Cat AI": "Yes" if s.categorization_ai_done else "—",
                        "Cat ✓": "Yes" if s.categorization_human_approved else "—",
                        "# Txns": n,
                    }
                )
            with st.expander("Statements overview (all PDFs in this session)", expanded=False):
                st.dataframe(overview, use_container_width=True, hide_index=True)

            pick = st.selectbox(
                "Statement",
                stmt_ids,
                format_func=lambda i: next(
                    (f"{s.institution or '—'} — {s.original_filename}" for s in stmts if s.id == i),
                    i,
                ),
                key="extraction_pick_stmt",
            )
            cur = next(s for s in stmts if s.id == pick)
            stmt_tx = [t for t in txns if t.statement_id == pick]
            pdf_path = Path(cur.pdf_storage_path) if cur.pdf_storage_path else None

            if st.button(
                "Run AI extraction for this statement",
                type="primary",
                disabled=not settings.gemini_api_key,
                key=f"btn_ext_{pick}",
            ):
                with st.spinner("Extracting…"):
                    run_extraction_for_statement(sid, pick)
                st.rerun()

            if (
                cur.extraction_status == "extracted"
                and stmt_tx
                and settings.gemini_api_key
            ):
                if st.button(
                    "Re-run AI description cleanup only",
                    key=f"btn_recln_{pick}",
                ):
                    with st.spinner("Cleaning descriptions…"):
                        run_description_cleanup_for_statement(sid, pick)
                    st.rerun()

            if cur.extraction_status == "extracted" and not cur.extraction_human_approved:
                st.markdown(
                    "**Review extraction** — PDF, amounts/dates vs statement, **payee**, and **descriptions**."
                )
                unresolved_desc = (
                    [t for t in stmt_tx if not _extraction_description_resolved(t)]
                    if stmt_tx
                    else []
                )
                unresolved_payee = (
                    [t for t in stmt_tx if not _extraction_payee_resolved(t)]
                    if stmt_tx
                    else []
                )
                if unresolved_desc:
                    st.warning(
                        f"{len(unresolved_desc)} transaction(s) still need **description** review "
                        "(save below, or high AI cleanup confidence / client clarification per row)."
                    )
                if unresolved_payee:
                    st.warning(
                        f"{len(unresolved_payee)} transaction(s) still need **payee** review "
                        "(edit **Payee (review)** and save, use **Accept extracted payees**, or client clarification)."
                    )
                clar_n = sum(1 for t in stmt_tx if t.client_clarification)
                if clar_n:
                    st.info(
                        f"{clar_n} row(s) marked for **client clarification** — listed on **Finalize** for export."
                    )
                page_ext = st.number_input(
                    "PDF page", min_value=1, value=1, step=1, key=f"page_ext_{pick}"
                )
                _nrows = len(stmt_tx)
                _pane_h = _split_pane_scroll_height(_nrows)
                c_pdf, c_rows = st.columns([0.38, 0.62])
                with c_pdf:
                    if pdf_path and pdf_path.exists():
                        render_pdf_html(pdf_path, int(page_ext), height=_pane_h)
                    else:
                        st.warning("PDF path missing.")
                with c_rows:
                    st.caption(
                        "Edit **Payee (review)** and **Clean description**; check **Client clarification** as needed, "
                        "then **Save description review**."
                    )
                    if stmt_tx:
                        ed_h = _pane_h
                        edited_ex = st.data_editor(
                            _extraction_review_table_rows(stmt_tx),
                            key=f"extraction_editor_{pick}",
                            hide_index=True,
                            num_rows="fixed",
                            use_container_width=True,
                            height=ed_h,
                            column_config=_extraction_review_column_config(
                                date_editable=False
                            ),
                        )
                        ex_rows = _data_editor_output_as_rows(edited_ex)
                        if st.button(
                            "Save description review",
                            type="primary",
                            key=f"save_ext_desc_{pick}",
                        ):
                            err = _persist_extraction_review_edits(
                                stmt_tx, ex_rows, update_txn_date=False
                            )
                            if err:
                                st.error(err)
                            else:
                                st.success("Saved.")
                                st.rerun()
                        if st.button(
                            "Accept extracted payees → Payee (review) and mark reviewed",
                            key=f"accept_payees_{pick}",
                        ):
                            with session_scope() as db:
                                for t in stmt_tx:
                                    row = db.get(TransactionORM, t.id)
                                    if not row or not (row.payee or "").strip():
                                        continue
                                    if not (row.payee_normalized or "").strip():
                                        row.payee_normalized = (row.payee or "").strip()
                                    row.payee_staff_accepted = True
                            st.rerun()
                        if st.button(
                            "Apply AI cleaned → clean description (high confidence only)",
                            key=f"apply_ai_hi_{pick}",
                        ):
                            with session_scope() as db:
                                for t in stmt_tx:
                                    if (t.description_cleanup_confidence or "").lower() != "high":
                                        continue
                                    row = db.get(TransactionORM, t.id)
                                    if (
                                        row
                                        and (row.description_ai_cleaned or "").strip()
                                    ):
                                        row.normalized_description = (
                                            row.description_ai_cleaned.strip()
                                        )
                                        row.description_staff_accepted = True
                                        row.client_clarification = False
                            st.rerun()
                    else:
                        st.caption("No transaction rows.")
                    st.caption(f"{len(stmt_tx)} row(s) — compare to the statement PDF.")
                    st.checkbox(
                        "Extracted rows match the PDF for this statement.",
                        key=f"chk_ext_{pick}",
                    )
                    review_gate = _statement_extraction_review_resolved(stmt_tx)
                    if not review_gate and stmt_tx:
                        st.caption(
                            "Approve is disabled until every row passes **description** and **payee** review rules."
                        )
                    if st.button(
                        "Approve extraction for this statement",
                        disabled=not (
                            st.session_state.get(f"chk_ext_{pick}", False) and review_gate
                        ),
                        key=f"btn_ap_ext_{pick}",
                    ):
                        with session_scope() as db:
                            row_s = db.get(StatementORM, pick)
                            if row_s:
                                row_s.extraction_human_approved = True
                        st.rerun()
            elif cur.extraction_human_approved and cur.extraction_status == "extracted":
                st.success("Extraction approved for this statement ✓")
                _ext_unlock_key = f"extraction_edit_unlocked_{pick}"
                _unlocked = bool(st.session_state.get(_ext_unlock_key, False))
                _b_u, _b_l, _ = st.columns([1, 1, 4])
                with _b_u:
                    if not _unlocked and st.button(
                        "Unlock to edit",
                        type="primary",
                        key=f"unlock_ext_{pick}",
                    ):
                        st.session_state[_ext_unlock_key] = True
                        st.rerun()
                with _b_l:
                    if _unlocked and st.button(
                        "Lock (read-only)",
                        key=f"lock_ext_{pick}",
                    ):
                        st.session_state[_ext_unlock_key] = False
                        st.rerun()
                if _unlocked:
                    st.caption(
                        "**Unlocked** — edit cells, then **Save changes**. Dates must be **YYYY-MM-DD**."
                    )
                else:
                    st.caption(
                        "**Locked** — view only. Click **Unlock to edit** to change date, payee, "
                        "clean description, or client clarification. After **Finalize**, use **Categorization & Review** for schedules and more columns."
                    )
                if stmt_tx:
                    page_ext_ro = st.number_input(
                        "PDF page",
                        min_value=1,
                        value=1,
                        step=1,
                        key=f"page_ext_ro_{pick}",
                    )
                    _nrows_ro = len(stmt_tx)
                    _pane_ro = _split_pane_scroll_height(_nrows_ro)
                    c_pdf_ro, c_rows_ro = st.columns([0.38, 0.62])
                    with c_pdf_ro:
                        if pdf_path and pdf_path.exists():
                            render_pdf_html(
                                pdf_path, int(page_ext_ro), height=_pane_ro
                            )
                        else:
                            st.warning("PDF path missing.")
                    with c_rows_ro:
                        if _unlocked:
                            edited_apr = st.data_editor(
                                _extraction_review_table_rows(stmt_tx),
                                key=f"extraction_editor_appr_{pick}",
                                hide_index=True,
                                num_rows="fixed",
                                use_container_width=True,
                                height=_pane_ro,
                                column_config=_extraction_review_column_config(
                                    date_editable=True
                                ),
                            )
                            ex_apr = _data_editor_output_as_rows(edited_apr)
                            if st.button(
                                "Save changes",
                                type="primary",
                                key=f"save_ext_appr_{pick}",
                            ):
                                err = _persist_extraction_review_edits(
                                    stmt_tx, ex_apr, update_txn_date=True
                                )
                                if err:
                                    st.error(err)
                                else:
                                    st.success("Saved.")
                                    st.rerun()
                        else:
                            st.dataframe(
                                _extraction_review_table_rows(stmt_tx),
                                use_container_width=True,
                                hide_index=True,
                                height=_pane_ro,
                            )
                        st.caption(f"{len(stmt_tx)} transaction row(s).")
                else:
                    st.caption("No transaction rows on file for this statement.")
            elif str(cur.extraction_status).startswith("error"):
                st.error(f"Extraction error: {cur.extraction_status}")

    with tab_categorization:
        st.subheader("Categorization & review")
        st.caption(
            "After **Extraction** is approved, run **AI categorization** and confirm schedules (summary grid + PDF). "
            "Use **Finalize** for session reconciliation; when reconciliation is approved, use **Line-by-line verification** "
            "below on this tab for the full editable grid, then **Export**."
        )
        if not settings.gemini_api_key:
            st.warning("Set GEMINI_API_KEY in the environment to enable AI categorization.")

        stmt_ids_cat = [s.id for s in stmts if s.pdf_storage_path]
        if not stmt_ids_cat:
            st.info("Upload at least one PDF on the **Upload** tab.")
        else:
            pick_c = st.selectbox(
                "Statement",
                stmt_ids_cat,
                format_func=lambda i: next(
                    (f"{s.institution or '—'} — {s.original_filename}" for s in stmts if s.id == i),
                    i,
                ),
                key="categorization_pick_stmt",
            )
            cur_c = next(s for s in stmts if s.id == pick_c)
            stmt_tx_c = [t for t in txns if t.statement_id == pick_c]
            pdf_path_c = Path(cur_c.pdf_storage_path) if cur_c.pdf_storage_path else None

            page_unified = st.number_input(
                "PDF page",
                min_value=1,
                value=1,
                step=1,
                key=f"cat_review_pdf_page_{pick_c}",
            )

            cat_disabled = (
                not cur_c.extraction_human_approved
                or cur_c.extraction_status != "extracted"
                or not settings.gemini_api_key
            )
            if st.button(
                "Run AI categorization for this statement",
                type="primary",
                disabled=cat_disabled,
                key=f"btn_cat_{pick_c}",
            ):
                with st.spinner("Categorizing…"):
                    run_categorization_for_statement(sid, pick_c)
                st.rerun()

            if cur_c.categorization_ai_done and not cur_c.categorization_human_approved:
                st.markdown("**Review categorization** — PDF and suggested schedules.")
                _ncat = len(stmt_tx_c)
                _cat_h = _split_pane_scroll_height(_ncat)
                c_pdf2, c_rows2 = st.columns([0.38, 0.62])
                with c_pdf2:
                    if pdf_path_c and pdf_path_c.exists():
                        render_pdf_html(
                            pdf_path_c, int(page_unified), height=_cat_h
                        )
                    else:
                        st.warning("PDF path missing.")
                with c_rows2:
                    st.dataframe(
                        [
                            {
                                "Date": t.txn_date,
                                "Payee": ((_effective_payee(t) or "")[:80]),
                                "Description": (
                                    (_effective_description_for_category(t) or "")[:100]
                                ),
                                "Amount": t.amount,
                                "Schedule": t.schedule,
                                "Confidence": t.confidence,
                            }
                            for t in stmt_tx_c
                        ],
                        use_container_width=True,
                        hide_index=True,
                        height=_cat_h,
                    )
                    st.checkbox(
                        "Schedules look reasonable for this statement (fine-tune categories in **Line-by-line verification** below).",
                        key=f"chk_cat_{pick_c}",
                    )
                    if st.button(
                        "Approve categorization for this statement",
                        disabled=not st.session_state.get(f"chk_cat_{pick_c}", False),
                        key=f"btn_ap_cat_{pick_c}",
                    ):
                        with session_scope() as db:
                            row_s = db.get(StatementORM, pick_c)
                            if row_s:
                                row_s.categorization_human_approved = True
                        st.rerun()
            elif cur_c.categorization_human_approved:
                st.success("Categorization approved for this statement ✓")
            elif not cur_c.extraction_human_approved:
                st.info("Approve **Extraction** for this statement first.")

            st.divider()
            st.markdown("##### Line-by-line verification")
            if sess.status in (ST_DRAFT, ST_PROCESSING, ST_RECONCILIATION_COMPLETE):
                st.info(
                    "Complete **Finalize** through reconciliation approval for session-wide checks. "
                    "You can still edit schedules and fields below; **Export** stays gated until verification rules pass."
                )
            elif sess.status == ST_PENDING_REVIEW:
                st.success(
                    "Reconciliation is approved — verify each transaction (or mark excluded), then use **Export**."
                )
            rec_data_cat = []
            if sess.reconciliation_json:
                try:
                    rec_data_cat = json.loads(sess.reconciliation_json)
                except Exception:
                    pass
            if rec_data_cat:
                with st.expander("Reconciliation summary", expanded=False):
                    st.json(rec_data_cat)

            filt_c = st.radio(
                "Filter rows",
                ["all", "high", "needs_review", "unverified"],
                horizontal=True,
                key=f"review_filt_{pick_c}",
            )
            right_tx = list(stmt_tx_c)
            if filt_c == "high":
                right_tx = [t for t in right_tx if (t.confidence or "") == "high"]
            elif filt_c == "needs_review":
                right_tx = [
                    t
                    for t in right_tx
                    if (t.confidence or "") == "low"
                    or (t.schedule or "") in ("needs_review", "?", "")
                ]
            elif filt_c == "unverified":
                right_tx = [t for t in right_tx if not t.verified]

            pdf_col, table_col = st.columns([0.38, 0.62])
            with pdf_col:
                st.markdown("**Source PDF**")
                _rev_h = _split_pane_scroll_height(len(right_tx) if right_tx else 1)
                if pdf_path_c and pdf_path_c.exists():
                    render_pdf_html(pdf_path_c, int(page_unified), height=_rev_h)
                elif pdf_path_c:
                    st.warning("PDF file missing on disk.")
            with table_col:
                st.caption(
                    "Table: **Date / Description / Payee / Amount** (read-only except Payee + Normalized), "
                    "AI **Category** + **AI conf**, **Subcategory**, **Normalized**, **Notes**, **Verified**, **Excluded**. "
                    "Edit cells, then **Save changes**."
                )
                if not right_tx:
                    st.caption("No rows match this filter.")
                else:
                    sch_opts = list(SCHEDULE_UI_OPTIONS)
                    editor_height = _split_pane_scroll_height(len(right_tx))
                    edited = st.data_editor(
                        _review_tx_table_rows(right_tx),
                        key=f"review_editor_{pick_c}_{filt_c}",
                        hide_index=True,
                        num_rows="fixed",
                        use_container_width=True,
                        height=editor_height,
                        column_config={
                            "Date": st.column_config.TextColumn("Date", disabled=True, width="small"),
                            "Description": st.column_config.TextColumn(
                                "Description", disabled=True, width="large"
                            ),
                            "Payee": st.column_config.TextColumn("Payee", width="medium"),
                            "Amount": st.column_config.NumberColumn(
                                "Amount", disabled=True, format="$%.2f", width="small"
                            ),
                            "Pg": st.column_config.NumberColumn("Pg", disabled=True, width="small"),
                            "Category": st.column_config.SelectboxColumn(
                                "Category",
                                options=sch_opts,
                                required=True,
                                width="small",
                            ),
                            "AI conf": st.column_config.TextColumn(
                                "AI conf", disabled=True, width="small"
                            ),
                            "Subcategory": st.column_config.TextColumn(
                                "Subcategory", width="medium"
                            ),
                            "Normalized": st.column_config.TextColumn(
                                "Normalized", width="medium"
                            ),
                            "Notes": st.column_config.TextColumn("Notes", width="medium"),
                            "Verified": st.column_config.CheckboxColumn("Verified", width="small"),
                            "Excluded": st.column_config.CheckboxColumn("Excluded", width="small"),
                        },
                    )
                    edited_rows = _data_editor_output_as_rows(edited)
                    if st.button("Save changes", type="primary", key=f"save_review_tbl_{pick_c}"):
                        if len(edited_rows) != len(right_tx):
                            st.error("Row count mismatch — refresh and try again.")
                        else:
                            with session_scope() as db:
                                for orig, erow in zip(right_tx, edited_rows):
                                    row = db.get(TransactionORM, orig.id)
                                    if not row:
                                        continue
                                    cat = erow.get("Category")
                                    if isinstance(cat, str) and cat in sch_opts:
                                        row.schedule = cat
                                    row.subcategory = (
                                        str(erow.get("Subcategory") or "").strip() or None
                                    )
                                    row.normalized_description = (
                                        str(erow.get("Normalized") or "").strip() or None
                                    )
                                    row.payee_normalized = (
                                        str(erow.get("Payee") or "").strip() or None
                                    )
                                    row.notes = (str(erow.get("Notes") or "").strip() or None)
                                    row.excluded = bool(erow.get("Excluded"))
                                    row.verified = bool(erow.get("Verified"))
                                    if row.verified:
                                        row.verified_by = user
                                        row.verified_at = _utcnow()
                                    else:
                                        row.verified_by = None
                                        row.verified_at = None
                                    row.edited_by_staff = True
                            st.success("Saved.")
                            st.rerun()

            stmt_tx_all = stmt_tx_c
            if stmt_tx_all and st.button(
                "Approve all high-confidence on this statement",
                key=f"appr_hi_{pick_c}",
            ):
                with session_scope() as db:
                    for t in stmt_tx_all:
                        if (t.confidence or "") == "high":
                            row = db.get(TransactionORM, t.id)
                            if row:
                                row.verified = True
                                row.verified_by = user
                                row.verified_at = _utcnow()
                st.rerun()

    with tab_finalize:
        st.subheader("Finalize session — combine data and reconcile")
        st.caption(
            "When **every** statement has extraction + categorization approved, run **reconciliation** "
            "to combine data across statements. Then approve reconciliation to unlock line-by-line verification "
            "on **Categorization & Review** and **Export**."
        )
        st.metric("Session stage", sess.status.replace("_", " ").title())

        stmt_ids_f = [s.id for s in stmts if s.pdf_storage_path]
        clar_rows = _client_clarification_export_rows(txns, stmts)
        if clar_rows:
            st.markdown("##### Client clarification list")
            st.dataframe(clar_rows, use_container_width=True, hide_index=True)
            buf = io.StringIO()
            if clar_rows:
                w = csv.DictWriter(buf, fieldnames=list(clar_rows[0].keys()))
                w.writeheader()
                w.writerows(clar_rows)
            st.download_button(
                "Download client clarification CSV",
                data=buf.getvalue().encode("utf-8"),
                file_name=f"client_clarification_{sid[:8]}.csv",
                mime="text/csv",
                key="dl_client_clar_csv",
            )
        else:
            st.caption("No transactions marked for client clarification.")

        st.divider()
        st.markdown("##### Reconciliation (all statements must be complete)")
        recon_ready = all_statements_ready_for_reconciliation(stmts)
        if not recon_ready and stmt_ids_f:
            st.info(
                "Complete **Extraction** + **Categorization** (with approvals) for **each** statement first."
            )
        if st.button(
            "Run reconciliation for entire session",
            type="primary",
            disabled=not recon_ready,
            key="btn_recon_session",
        ):
            if not all_statements_ready_for_reconciliation(stmts):
                st.error(
                    "Every statement must be extracted, extraction-approved, categorized, and categorization-approved."
                )
            else:
                with st.spinner("Reconciling…"):
                    run_reconciliation_stage(sid)
                st.success("Reconciliation finished.")
                st.rerun()

        if sess.status == ST_RECONCILIATION_COMPLETE:
            rec_data = []
            if sess.reconciliation_json:
                try:
                    rec_data = json.loads(sess.reconciliation_json)
                except Exception:
                    pass
            if rec_data:
                st.json(rec_data)
            else:
                st.info("No reconciliation issues recorded.")
            st.checkbox(
                "I have reviewed reconciliation results (and overrides on **Categorization & Review** if needed).",
                key=f"approve_rec_{sid}",
            )
            if st.button(
                "Approve reconciliation → line-by-line verification",
                disabled=not st.session_state.get(f"approve_rec_{sid}", False),
                key="btn_appr_rec",
            ):
                with session_scope() as db:
                    row = db.get(AccountingSessionORM, sid)
                    if row and row.status == ST_RECONCILIATION_COMPLETE:
                        row.status = ST_PENDING_REVIEW
                st.rerun()

        elif sess.status in (ST_PENDING_REVIEW, ST_COMPLETED):
            st.success(
                "Reconciliation approved ✓ — continue on **Categorization & Review** (line-by-line verification), then **Export**."
            )

        if is_admin_user(user):
            with st.expander("Admin: skip review gates (dev only)"):
                st.caption(
                    "Marks every statement approved; all description + payee rows staff-accepted; session **pending_review**."
                )
                if st.button("Force all statement approvals + pending_review", key="adm_force"):
                    with session_scope() as db:
                        row = db.get(AccountingSessionORM, sid)
                        if row:
                            for s in db.query(StatementORM).filter_by(session_id=sid).all():
                                if s.pdf_storage_path:
                                    s.extraction_human_approved = True
                                    s.categorization_ai_done = True
                                    s.categorization_human_approved = True
                            for t in (
                                db.query(TransactionORM)
                                .join(StatementORM)
                                .filter(StatementORM.session_id == sid)
                                .all()
                            ):
                                t.description_staff_accepted = True
                                t.payee_staff_accepted = True
                            if row.status not in (ST_DRAFT, ST_COMPLETED):
                                row.status = ST_PENDING_REVIEW
                    st.rerun()

    with tab_export:
        st.subheader("Excel export")
        needs_action = [t for t in txns if not t.verified and not t.excluded]
        if needs_action:
            st.warning(
                f"{len(needs_action)} transaction(s) must be verified or marked excluded before export."
            )
        export_disabled = bool(needs_action)
        if st.button("Generate Excel", disabled=export_disabled, type="primary"):
            with session_scope() as db:
                full_tx = (
                    db.query(TransactionORM)
                    .join(StatementORM)
                    .filter(StatementORM.session_id == sid)
                    .all()
                )
                full_st = db.query(StatementORM).filter_by(session_id=sid).all()
            tdicts = [_transaction_to_dict(t) for t in full_tx]
            sdict = {s.id: _statement_to_dict(s) for s in full_st}
            stmt_order = [s.id for s in sorted(stmts, key=lambda x: (x.sort_order, x.id))]
            session_meta = {
                "case_number": getattr(sess, "case_number", None),
                "accounting_type": getattr(sess, "accounting_type", None),
                "fiduciary_name": getattr(sess, "fiduciary_name", None),
                "fiduciary_role": getattr(sess, "fiduciary_role", None),
            }
            out = generate_accounting_workbook(
                matter_type=sess.matter_type,
                matter_name=sess.matter_name,
                period_start=sess.period_start,
                period_end=sess.period_end,
                transactions=tdicts,
                statement_by_id=sdict,
                mapping_path=settings.template_mapping_path,
                verifier_email=user,
                statement_order=stmt_order,
                session_meta=session_meta,
            )
            xbytes = out.read_bytes()
            st.download_button(
                "Download workbook",
                data=xbytes,
                file_name=out.name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            with session_scope() as db:
                srow = db.get(AccountingSessionORM, sid)
                if srow:
                    srow.status = "completed"
        if is_admin_user(user):
            st.divider()
            st.subheader("Admin")
            st.json(load_admin_settings())


def _schedule_value_for_editor(current: Optional[str]) -> str:
    """Map DB schedule to a value allowed in Review table SelectboxColumn."""
    opts = list(SCHEDULE_UI_OPTIONS)
    if not current or current == "H":
        return "needs_review"
    if current in opts:
        return current
    return "needs_review"


def _review_tx_table_rows(right_tx: list[TransactionORM]) -> list[dict[str, Any]]:
    """Build rows for st.data_editor (insertion order = column order)."""
    rows: list[dict[str, Any]] = []
    for t in right_tx:
        desc = (t.description or "").strip()
        if len(desc) > 200:
            desc = desc[:197] + "…"
        rows.append(
            {
                "Date": t.txn_date.isoformat() if t.txn_date else "",
                "Description": desc,
                "Payee": _effective_payee(t) or "",
                "Amount": float(t.amount) if t.amount is not None else None,
                "Pg": int(t.source_page) if t.source_page is not None else None,
                "Category": _schedule_value_for_editor(t.schedule),
                "AI conf": (t.confidence or "—").strip() or "—",
                "Subcategory": t.subcategory or "",
                "Normalized": t.normalized_description or "",
                "Notes": t.notes or "",
                "Verified": bool(t.verified),
                "Excluded": bool(t.excluded),
            }
        )
    return rows


def _data_editor_output_as_rows(edited: Any) -> list[dict[str, Any]]:
    """Normalize st.data_editor return value (list of dicts or DataFrame)."""
    if edited is None:
        return []
    if hasattr(edited, "to_dict"):
        recs = edited.to_dict("records")
        return [dict(r) for r in recs]
    return [dict(r) for r in edited]


if __name__ == "__main__":
    main()
