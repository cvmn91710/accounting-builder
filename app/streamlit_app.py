"""Golden Oaks Probate Accounting — Streamlit entrypoint."""

from __future__ import annotations

import base64
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import streamlit as st
import streamlit.components.v1 as components

from app import bootstrap_templates
from app.admin_settings_store import is_admin_user, load_admin_settings
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
from app.text_extract import extract_pdf


st.set_page_config(
    page_title="Golden Oaks | Probate Accounting",
    page_icon="📑",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
        st.warning("PDF not found on disk.")
        return
    b64 = base64.b64encode(pdf_path.read_bytes()).decode("utf-8")
    h = 720
    components.html(
        f"""
        <iframe src="data:application/pdf;base64,{b64}#page={page}"
            width="100%" height="{h}" style="border:1px solid #ccc;"></iframe>
        """,
        height=h + 12,
    )


def run_ai_pipeline(session_id: str) -> None:
    settings = get_settings()
    if not settings.gemini_api_key:
        st.error("Configure GEMINI_API_KEY to run extraction and categorization.")
        return

    with session_scope() as db:
        sess = db.get(AccountingSessionORM, session_id)
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
                    t = TransactionORM(
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
                    db.add(t)
            except Exception as e:
                stt.extraction_status = f"error: {e}"
        sess.status = "pending_categorization"

    with session_scope() as db:
        sess = db.get(AccountingSessionORM, session_id)
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
            sess.status = "pending_review"
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
        sess.status = "pending_review"

    # Reconciliation
    with session_scope() as db:
        sess = db.get(AccountingSessionORM, session_id)
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
        sess.status = "pending_review"


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
            matter_id = st.text_input("Matter ID (optional)")
            matter_type = st.selectbox(
                "Matter type",
                ["probate_estate", "conservatorship", "trust_administration"],
            )
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

    st.caption(
        f"{sess.matter_name} | {sess.period_start} — {sess.period_end} | Type: {sess.matter_type}"
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
        st.subheader("AI extraction & categorization")
        if not settings.gemini_api_key:
            st.warning("Set GEMINI_API_KEY in the environment to enable AI processing.")
        if st.button("Run extraction + categorization + reconciliation", type="primary"):
            with st.spinner("Processing…"):
                run_ai_pipeline(sid)
            st.success("Pipeline complete (or see errors above).")
            st.rerun()

    # Review tab
    with tab_review:
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
                        [
                            "A",
                            "B",
                            "C",
                            "D",
                            "E",
                            "F",
                            "G",
                            "H",
                            "I",
                            "needs_review",
                            "internal_transfer",
                            "excluded",
                        ],
                        index=_schedule_index(t.schedule),
                        key=f"sch_{t.id}",
                    )
                    sub = st.text_input("Subcategory", value=t.subcategory or "", key=f"sub_{t.id}")
                    notes = st.text_input("Notes", value=t.notes or "", key=f"n_{t.id}")
                    excl = st.checkbox("Excluded from schedules", value=t.excluded, key=f"e_{t.id}")
                    ver = st.checkbox("Verified", value=t.verified, key=f"v_{t.id}")
                    if st.button("Save row", key=f"save_{t.id}"):
                        with session_scope() as db:
                            row = db.get(TransactionORM, t.id)
                            if row:
                                row.schedule = sch
                                row.subcategory = sub or None
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
            out = generate_accounting_workbook(
                matter_type=sess.matter_type,
                matter_name=sess.matter_name,
                period_start=sess.period_start,
                period_end=sess.period_end,
                transactions=tdicts,
                statement_by_id=sdict,
                mapping_path=settings.template_mapping_path,
                verifier_email=user,
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
    opts = [
        "A",
        "B",
        "C",
        "D",
        "E",
        "F",
        "G",
        "H",
        "I",
        "needs_review",
        "internal_transfer",
        "excluded",
    ]
    if not current:
        return 9
    try:
        return opts.index(current)
    except ValueError:
        return 9


if __name__ == "__main__":
    main()
