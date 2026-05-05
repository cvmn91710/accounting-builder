"""Golden Oaks Probate Accounting — Streamlit entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path

# Streamlit puts the script's directory on sys.path, not the project root, so `import app` fails in Docker/Coolify unless PYTHONPATH is set. Ensure repo root is importable.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import base64
import hashlib
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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Session workflow (human review gates after extraction, categorization, reconciliation)
ST_DRAFT = "draft"
ST_EXTRACTION_COMPLETE = "extraction_complete"
ST_EXTRACTION_APPROVED = "extraction_approved"
ST_CATEGORIZATION_COMPLETE = "categorization_complete"
ST_CATEGORIZATION_APPROVED = "categorization_approved"
ST_RECONCILIATION_COMPLETE = "reconciliation_complete"
ST_PENDING_REVIEW = "pending_review"
ST_COMPLETED = "completed"


def migrate_workflow_status(sid: str) -> None:
    """Map legacy statuses to the gated workflow."""
    with session_scope() as db:
        row = db.get(AccountingSessionORM, sid)
        if not row:
            return
        if row.status == "pending_categorization":
            row.status = ST_EXTRACTION_COMPLETE


def run_extraction_stage(session_id: str) -> None:
    settings = get_settings()
    if not settings.gemini_api_key:
        st.error("Configure GEMINI_API_KEY to run extraction.")
        return
    with session_scope() as db:
        sess = db.get(AccountingSessionORM, session_id)
        if not sess:
            return
        sess.reconciliation_json = None
        stmts = (
            db.query(StatementORM)
            .filter_by(session_id=session_id)
            .order_by(StatementORM.sort_order)
            .all()
        )
        for stt in stmts:
            if not stt.pdf_storage_path or not Path(stt.pdf_storage_path).exists():
                stt.extraction_status = "error"
                continue
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
                for ex in result.transactions:
                    ta = ex.model_dump()
                    db.add(
                        TransactionORM(
                            id=new_id(),
                            statement_id=stt.id,
                            txn_date=ta.get("date"),
                            description=ta.get("description"),
                            amount=ta.get("amount"),
                            txn_type=ta.get("txn_type"),
                            balance_after=ta.get("balance"),
                            source_page=ta.get("source_page"),
                            security_symbol=ta.get("security_symbol"),
                            quantity=ta.get("quantity"),
                            price=ta.get("price"),
                            cost_basis=ta.get("cost_basis"),
                        )
                    )
                if not result.transactions:
                    st.warning(
                        f"**{stt.original_filename}**: extraction returned **0 transactions**. "
                        "The PDF may be image-only (try a clearer scan), or table detection failed."
                    )
            except Exception as e:
                stt.extraction_status = f"error: {e}"
        sess.status = ST_EXTRACTION_COMPLETE


def run_categorization_stage(session_id: str) -> None:
    settings = get_settings()
    if not settings.gemini_api_key:
        st.error("Configure GEMINI_API_KEY to run categorization.")
        return
    with session_scope() as db:
        sess = db.get(AccountingSessionORM, session_id)
        if not sess:
            return
        stmts = db.query(StatementORM).filter_by(session_id=session_id).all()
        txns = (
            db.query(TransactionORM)
            .join(StatementORM)
            .filter(StatementORM.session_id == session_id)
            .all()
        )
        payload = []
        for t in txns:
            payload.append(
                {
                    "transactionId": t.id,
                    "date": t.txn_date.isoformat() if t.txn_date else None,
                    "description": t.description,
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
        if not payload:
            sess.status = ST_CATEGORIZATION_COMPLETE
            return
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
        sess.status = ST_CATEGORIZATION_COMPLETE


def run_reconciliation_stage(session_id: str) -> None:
    with session_scope() as db:
        sess = db.get(AccountingSessionORM, session_id)
        if not sess:
            return
        stmts = db.query(StatementORM).filter_by(session_id=session_id).all()
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


def render_pdf_html(pdf_path: Path, page: int = 1) -> None:
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
    h = 720
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

    tab_upload, tab_run, tab_review, tab_export = st.tabs(
        ["Upload", "Process", "Review", "Export"]
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

    with tab_run:
        st.subheader("AI pipeline (human review gates)")
        st.caption(
            "Run **extraction**, then **categorization**, then **reconciliation** — each step requires an explicit "
            "**approve** before the next. Use the **Review** tab to spot-check PDFs vs rows after extraction, "
            "then again for schedule verification before export."
        )
        if not settings.gemini_api_key:
            st.warning("Set GEMINI_API_KEY in the environment to enable AI extraction and categorization.")
        st.metric("Current stage", sess.status.replace("_", " ").title())

        ready_pdfs = bool(stmts) and any(s.pdf_storage_path for s in stmts)

        st.markdown("##### Step 1 — Extraction")
        st.caption(
            "Pulls transactions from each PDF via Gemini. Re-running replaces all transactions and clears later steps."
        )
        if st.button(
            "Run AI extraction",
            type="primary",
            disabled=not ready_pdfs or not settings.gemini_api_key,
        ):
            with st.spinner("Extracting…"):
                run_extraction_stage(sid)
            st.success("Extraction finished. Review the summary below, then approve.")
            st.rerun()

        if sess.status == ST_EXTRACTION_COMPLETE:
            ext_rows = []
            n_txn = 0
            for s in stmts:
                c = sum(1 for t in txns if t.statement_id == s.id)
                n_txn += c
                ext_rows.append(
                    {
                        "File": s.original_filename,
                        "Extraction": s.extraction_status,
                        "Transactions": c,
                    }
                )
            st.dataframe(ext_rows, use_container_width=True, hide_index=True)
            st.checkbox(
                "I have reviewed the extraction summary (counts and statement errors).",
                key=f"approve_extraction_{sid}",
            )
            if st.button(
                "Approve extraction → unlock categorization",
                disabled=not st.session_state.get(f"approve_extraction_{sid}", False),
            ):
                with session_scope() as db:
                    row = db.get(AccountingSessionORM, sid)
                    if row and row.status == ST_EXTRACTION_COMPLETE:
                        row.status = ST_EXTRACTION_APPROVED
                st.rerun()

        elif sess.status in (
            ST_EXTRACTION_APPROVED,
            ST_CATEGORIZATION_COMPLETE,
            ST_CATEGORIZATION_APPROVED,
            ST_RECONCILIATION_COMPLETE,
            ST_PENDING_REVIEW,
            ST_COMPLETED,
        ):
            st.success("Extraction approved ✓")

        st.markdown("##### Step 2 — Categorization")
        st.caption("Assigns California schedule letters (Gemini). Requires approved extraction.")
        if st.button(
            "Run AI categorization",
            type="primary",
            disabled=sess.status != ST_EXTRACTION_APPROVED or not settings.gemini_api_key,
        ):
            with st.spinner("Categorizing…"):
                run_categorization_stage(sid)
            st.success("Categorization finished. Review suggested schedules, then approve.")
            st.rerun()

        if sess.status == ST_CATEGORIZATION_COMPLETE:
            by_sched: dict[str, int] = {}
            for t in txns:
                k = (t.schedule or "—").strip()
                by_sched[k] = by_sched.get(k, 0) + 1
            st.dataframe(
                [{"Schedule": k, "Count": v} for k, v in sorted(by_sched.items())],
                use_container_width=True,
                hide_index=True,
            )
            st.checkbox(
                "I have reviewed AI schedules and confidence (use Review tab to drill in).",
                key=f"approve_cat_{sid}",
            )
            if st.button(
                "Approve categorization → unlock reconciliation",
                disabled=not st.session_state.get(f"approve_cat_{sid}", False),
            ):
                with session_scope() as db:
                    row = db.get(AccountingSessionORM, sid)
                    if row and row.status == ST_CATEGORIZATION_COMPLETE:
                        row.status = ST_CATEGORIZATION_APPROVED
                st.rerun()

        elif sess.status in (
            ST_CATEGORIZATION_APPROVED,
            ST_RECONCILIATION_COMPLETE,
            ST_PENDING_REVIEW,
            ST_COMPLETED,
        ):
            st.success("Categorization approved ✓")

        st.markdown("##### Step 3 — Reconciliation")
        st.caption("Duplicates, internal transfers, balance hints. Requires approved categorization.")
        if st.button(
            "Run reconciliation",
            type="primary",
            disabled=sess.status != ST_CATEGORIZATION_APPROVED,
        ):
            with st.spinner("Reconciling…"):
                run_reconciliation_stage(sid)
            st.success("Reconciliation finished. Review issues, then approve.")
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
                "I have reviewed reconciliation results (and overrides in Review if needed).",
                key=f"approve_rec_{sid}",
            )
            if st.button(
                "Approve reconciliation → line-by-line verification",
                disabled=not st.session_state.get(f"approve_rec_{sid}", False),
            ):
                with session_scope() as db:
                    row = db.get(AccountingSessionORM, sid)
                    if row and row.status == ST_RECONCILIATION_COMPLETE:
                        row.status = ST_PENDING_REVIEW
                st.rerun()

        elif sess.status in (ST_PENDING_REVIEW, ST_COMPLETED):
            st.success("Reconciliation approved ✓ — continue on the **Review** tab, then **Export**.")

        if is_admin_user(user):
            with st.expander("Admin: skip review gates (dev only)"):
                st.caption("Sets status to **pending_review** without human checkboxes — use only for testing.")
                if st.button("Force all gates approved"):
                    with session_scope() as db:
                        row = db.get(AccountingSessionORM, sid)
                        if row and row.status not in (ST_DRAFT, ST_COMPLETED):
                            row.status = ST_PENDING_REVIEW
                    st.rerun()

    # Review tab
    with tab_review:
        if sess.status in (
            ST_EXTRACTION_COMPLETE,
            ST_EXTRACTION_APPROVED,
            ST_CATEGORIZATION_COMPLETE,
            ST_CATEGORIZATION_APPROVED,
            ST_RECONCILIATION_COMPLETE,
        ):
            st.info(
                "You can compare PDFs to rows here at any time. "
                "Complete the **Process** tab approvals through reconciliation before final verification for export."
            )
        elif sess.status == ST_PENDING_REVIEW:
            st.success(
                "Reconciliation is approved — verify each transaction (or mark excluded), then use **Export**."
            )
        rec_data = []
        if sess.reconciliation_json:
            try:
                rec_data = json.loads(sess.reconciliation_json)
            except Exception:
                pass
        if rec_data:
            with st.expander("Reconciliation summary", expanded=False):
                st.json(rec_data)

        stmt_ids = [s.id for s in stmts]
        if not stmt_ids:
            st.info("Upload statements first.")
        else:
            pick = st.selectbox(
                "Statement",
                stmt_ids,
                format_func=lambda i: next(
                    (f"{s.institution or '—'} — {s.original_filename}" for s in stmts if s.id == i),
                    i,
                ),
            )
            current = next(s for s in stmts if s.id == pick)
            pdf_path = Path(current.pdf_storage_path) if current.pdf_storage_path else None
            page_hint = st.number_input("PDF page", min_value=1, value=1, step=1)

            filt = st.radio(
                "Filter",
                ["all", "high", "needs_review", "unverified"],
                horizontal=True,
            )
            right_tx = [t for t in txns if t.statement_id == pick]
            if filt == "high":
                right_tx = [t for t in right_tx if (t.confidence or "") == "high"]
            elif filt == "needs_review":
                right_tx = [
                    t
                    for t in right_tx
                    if (t.confidence or "") == "low"
                    or (t.schedule or "") in ("needs_review", "?", "")
                ]
            elif filt == "unverified":
                right_tx = [t for t in right_tx if not t.verified]

            c1, c2 = st.columns([1, 1])
            with c1:
                if pdf_path:
                    render_pdf_html(pdf_path, int(page_hint))
            with c2:
                st.markdown("**Transactions**")
                for t in right_tx:
                    keyp = f"{t.id}_row"
                    col_a, col_b = st.columns([3, 1])
                    with col_a:
                        st.write(
                            f"**{t.txn_date or '—'}** {t.description or ''} — **{t.amount}**"
                        )
                        conf = t.confidence or "—"
                        st.caption(f"Schedule: {t.schedule} | Confidence: {conf}")
                    with col_b:
                        if st.button("Go to page", key=f"gp_{t.id}"):
                            st.session_state["jump_page"] = t.source_page or 1
                    if "jump_page" in st.session_state and st.session_state.get("last_jump") != t.id:
                        pass
                st.divider()
                for t in right_tx:
                    sch = st.selectbox(
                        "Schedule",
                        list(SCHEDULE_UI_OPTIONS),
                        index=_schedule_index(t.schedule),
                        key=f"sch_{t.id}",
                    )
                    sub = st.text_input("Subcategory", value=t.subcategory or "", key=f"sub_{t.id}")
                    norm = st.text_input(
                        "Normalized description (optional)",
                        value=t.normalized_description or "",
                        key=f"nd_{t.id}",
                    )
                    notes = st.text_input("Notes", value=t.notes or "", key=f"n_{t.id}")
                    excl = st.checkbox("Excluded from schedules", value=t.excluded, key=f"e_{t.id}")
                    ver = st.checkbox("Verified", value=t.verified, key=f"v_{t.id}")
                    if st.button("Save row", key=f"save_{t.id}"):
                        with session_scope() as db:
                            row = db.get(TransactionORM, t.id)
                            if row:
                                row.schedule = sch
                                row.subcategory = sub or None
                                row.normalized_description = norm or None
                                row.notes = notes or None
                                row.excluded = excl
                                row.verified = ver
                                if ver:
                                    row.verified_by = user
                                    row.verified_at = _utcnow()
                                row.edited_by_staff = True
                        st.success("Saved")

                stmt_tx_all = [t for t in txns if t.statement_id == pick]
                if st.button("Approve all high-confidence on this statement"):
                    with session_scope() as db:
                        for t in stmt_tx_all:
                            if (t.confidence or "") == "high":
                                row = db.get(TransactionORM, t.id)
                                if row:
                                    row.verified = True
                                    row.verified_by = user
                                    row.verified_at = _utcnow()
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


def _schedule_index(current: Optional[str]) -> int:
    opts = list(SCHEDULE_UI_OPTIONS)
    # Legacy sessions may still have "H" from older builds — map to needs_review for editing
    if current == "H":
        return opts.index("needs_review")
    if not current:
        return opts.index("needs_review")
    try:
        return opts.index(current)
    except ValueError:
        return opts.index("needs_review")


if __name__ == "__main__":
    main()
