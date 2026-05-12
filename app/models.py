"""Pydantic models for extraction, categorization, and API boundaries."""

from __future__ import annotations

import datetime
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

# --- Document type (per-statement classification) ---


class DocumentType(str, Enum):
    bank = "bank"
    brokerage = "brokerage"
    credit_card = "credit_card"
    retirement = "retirement"
    unknown = "unknown"


class TradeKind(str, Enum):
    buy = "buy"
    sell = "sell"
    dividend = "dividend"
    interest = "interest"
    cap_gain_dist = "cap_gain_dist"
    fee = "fee"
    transfer_in = "transfer_in"
    transfer_out = "transfer_out"
    cash = "cash"
    other = "other"


class AssetClass(str, Enum):
    cash = "cash"
    non_cash = "non_cash"


class PositionAsOf(str, Enum):
    beginning = "beginning"
    ending = "ending"


# --- Stage 1: extraction ---


class ExtractedTransaction(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    # Use datetime.date (not `date`) so the field name `date` does not shadow the type.
    date: Optional[datetime.date] = None
    description: Optional[str] = None
    payee: Optional[str] = None
    amount: Optional[Decimal] = None
    txn_type: Optional[str] = Field(default=None, alias="type")
    balance: Optional[Decimal] = None
    source_page: Optional[int] = Field(default=None, alias="sourcePage")
    # Brokerage extras
    security_symbol: Optional[str] = Field(default=None, alias="securitySymbol")
    quantity: Optional[Decimal] = None
    price: Optional[Decimal] = None
    cost_basis: Optional[Decimal] = Field(default=None, alias="costBasis")
    trade_kind: Optional[str] = Field(default=None, alias="tradeKind")
    proceeds: Optional[Decimal] = None
    realized_gain_loss: Optional[Decimal] = Field(default=None, alias="realizedGainLoss")


class ExtractedPosition(BaseModel):
    """One row in a Holdings snapshot (period start or period end)."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    as_of: Optional[str] = Field(default=None, alias="asOf")  # 'beginning' | 'ending'
    asset_class: Optional[str] = Field(default=None, alias="assetClass")  # 'cash' | 'non_cash'
    security_symbol: Optional[str] = Field(default=None, alias="securitySymbol")
    security_description: Optional[str] = Field(default=None, alias="securityDescription")
    quantity: Optional[Decimal] = None
    unit_price: Optional[Decimal] = Field(default=None, alias="unitPrice")
    market_value: Optional[Decimal] = Field(default=None, alias="marketValue")
    cost_basis: Optional[Decimal] = Field(default=None, alias="costBasis")
    source_page: Optional[int] = Field(default=None, alias="sourcePage")


class DocumentTypeDetection(BaseModel):
    """Result of the lightweight document-type detection Gemini call."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    document_type: DocumentType = Field(default=DocumentType.unknown, alias="documentType")
    institution: Optional[str] = None
    confidence: Optional[str] = None  # "high" | "medium" | "low"


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    institution: Optional[str] = None
    account_type: Optional[str] = Field(default=None, alias="accountType")
    account_number_last4: Optional[str] = Field(default=None, alias="accountNumberLast4")
    statement_period_start: Optional[date] = Field(default=None, alias="statementPeriodStart")
    statement_period_end: Optional[date] = Field(default=None, alias="statementPeriodEnd")
    beginning_balance: Optional[Decimal] = Field(default=None, alias="beginningBalance")
    ending_balance: Optional[Decimal] = Field(default=None, alias="endingBalance")
    document_type: DocumentType = Field(default=DocumentType.unknown, alias="documentType")
    document_type_confidence: Optional[str] = Field(default=None, alias="documentTypeConfidence")
    transactions: list[ExtractedTransaction] = Field(default_factory=list)
    beginning_holdings: list[ExtractedPosition] = Field(
        default_factory=list, alias="beginningHoldings"
    )
    ending_holdings: list[ExtractedPosition] = Field(
        default_factory=list, alias="endingHoldings"
    )
    flags: list[str] = Field(default_factory=list)


# --- Stage 2: categorization ---


class ConfidenceLevel(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


# --- Stage 1b: description cleanup (post-extraction; uses ConfidenceLevel) ---


class DescriptionCleanupItem(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    transaction_id: str = Field(alias="transactionId")
    cleaned_description: Optional[str] = Field(default=None, alias="cleanedDescription")
    confidence: ConfidenceLevel = ConfidenceLevel.medium
    reasoning: Optional[str] = None


class DescriptionCleanupResult(BaseModel):
    cleanups: list[DescriptionCleanupItem] = Field(default_factory=list)


class CategorizationItem(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    transaction_id: str = Field(alias="transactionId")
    schedule: str  # A-I, needs_review, internal_transfer, excluded
    subcategory: Optional[str] = None
    confidence: ConfidenceLevel = ConfidenceLevel.medium
    reasoning: Optional[str] = None


class CategorizationResult(BaseModel):
    categorizations: list[CategorizationItem] = Field(default_factory=list)


# --- Reconciliation ---


class ReconciliationIssueType(str, Enum):
    duplicate = "duplicate"
    internal_transfer = "internal_transfer"
    balance_mismatch = "balance_mismatch"
    period_gap = "period_gap"
    holdings_mismatch = "holdings_mismatch"
    realized_gain_loss_mismatch = "realized_gain_loss_mismatch"


class ReconciliationIssue(BaseModel):
    type: ReconciliationIssueType
    message: str
    transaction_ids: list[str] = Field(default_factory=list)
    amount_delta: Optional[Decimal] = None
    meta: dict[str, Any] = Field(default_factory=dict)


# --- Session metadata (lightweight) ---


class SessionMeta(BaseModel):
    matter_name: str
    matter_id: Optional[str] = None
    matter_type: str  # conservatorship | probate_estate | trust_administration
    accounting_type: Optional[str] = None  # First Account | Subsequent Account
    case_number: Optional[str] = None
    fiduciary_name: Optional[str] = None
    period_start: date
    period_end: date
