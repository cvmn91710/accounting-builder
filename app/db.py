"""SQLite persistence for accounting sessions and transactions."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from contextlib import contextmanager
from typing import Generator, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AccountingSessionORM(Base):
    __tablename__ = "accounting_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    matter_name: Mapped[str] = mapped_column(String(512))
    matter_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    matter_type: Mapped[str] = mapped_column(String(64))
    accounting_type: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )  # First Account / Subsequent Account
    case_number: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    fiduciary_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(64), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
    owner_email: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    reconciliation_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    statements: Mapped[list["StatementORM"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class StatementORM(Base):
    __tablename__ = "statements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("accounting_sessions.id"))
    original_filename: Mapped[str] = mapped_column(String(512))
    institution: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    account_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    account_last4: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    statement_period_start: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    statement_period_end: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    beginning_balance: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    ending_balance: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    extraction_status: Mapped[str] = mapped_column(String(32), default="pending_extraction")
    pdf_storage_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extraction_flags_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    session: Mapped["AccountingSessionORM"] = relationship(back_populates="statements")
    transactions: Mapped[list["TransactionORM"]] = relationship(
        back_populates="statement", cascade="all, delete-orphan"
    )


class TransactionORM(Base):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    statement_id: Mapped[str] = mapped_column(String(36), ForeignKey("statements.id"))
    txn_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    txn_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    balance_after: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    source_page: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    security_symbol: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    quantity: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    price: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    cost_basis: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)

    schedule: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    subcategory: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    confidence: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    ai_reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    normalized_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    internal_transfer: Mapped[bool] = mapped_column(Boolean, default=False)
    edited_by_staff: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    verified_by: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)

    statement: Mapped["StatementORM"] = relationship(back_populates="transactions")


_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        path = get_settings().sqlite_db_path
        path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            f"sqlite:///{path.resolve()}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(_engine)
        _migrate_sqlite_schema(_engine)
    return _engine


def _migrate_sqlite_schema(engine) -> None:
    """Add columns introduced after first deploy (SQLite has no ALTER IF NOT EXISTS)."""
    try:
        insp = inspect(engine)
    except Exception:
        return
    if insp.has_table("accounting_sessions"):
        cols = {c["name"] for c in insp.get_columns("accounting_sessions")}
        adds = []
        if "accounting_type" not in cols:
            adds.append("accounting_type VARCHAR(64)")
        if "case_number" not in cols:
            adds.append("case_number VARCHAR(256)")
        if "fiduciary_name" not in cols:
            adds.append("fiduciary_name VARCHAR(512)")
        for ddl in adds:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE accounting_sessions ADD COLUMN {ddl}"))
    insp = inspect(engine)
    if insp.has_table("transactions"):
        tcols = {c["name"] for c in insp.get_columns("transactions")}
        if "normalized_description" not in tcols:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "ALTER TABLE transactions ADD COLUMN normalized_description TEXT"
                    )
                )


@contextmanager
def session_scope() -> Generator:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    db = _SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def new_id() -> str:
    return str(uuid.uuid4())
