"""California fiduciary accounting schedule definitions — aligned with spec v1.2 (firm template)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

MatterType = Literal["conservatorship", "probate_estate", "trust_administration"]

# Standard template sheets (always present in master workbook)
STANDARD_SCHEDULE_LETTERS = frozenset({"A", "B", "C", "E", "F"})

# Added per case when transactions warrant (ad-hoc sheets)
AD_HOC_SCHEDULE_LETTERS = frozenset({"D", "G", "I", "K", "L", "P", "X"})

ALL_SCHEDULE_LETTERS = STANDARD_SCHEDULE_LETTERS | AD_HOC_SCHEDULE_LETTERS

# Streamlit / verifier dropdown order (spec v1.2 letters; no Schedule H in firm template)
SCHEDULE_UI_OPTIONS: tuple[str, ...] = (
    "A",
    "B",
    "C",
    "D",
    "E",
    "F",
    "G",
    "I",
    "K",
    "L",
    "P",
    "X",
    "needs_review",
    "internal_transfer",
    "excluded",
)

# Subset surfaced when reviewing transactions on a brokerage statement.
# Drops Schedule B/E (those are populated from the holdings tables, not transactions),
# F (additional property received — typically a manual entry), and I/L/P (trade-or-business
# / professional-fees scenarios that brokerage activity does not produce).
SCHEDULE_UI_OPTIONS_BROKERAGE: tuple[str, ...] = (
    "A",
    "C",
    "D",
    "G",
    "K",
    "X",
    "needs_review",
    "internal_transfer",
    "excluded",
)


def schedule_ui_options_for(document_type: Optional[str]) -> tuple[str, ...]:
    """Return the dropdown option list appropriate for the statement's document type."""
    if (document_type or "").lower() == "brokerage":
        return SCHEDULE_UI_OPTIONS_BROKERAGE
    return SCHEDULE_UI_OPTIONS

# Schedule B in the firm template = POH @ Beginning (not "Gains" — template wins per spec v1.2)


@dataclass(frozen=True)
class ScheduleDefinition:
    letter: str
    name: str
    standard_in_template: bool
    includes: str
    excludes: str


SCHEDULES: list[ScheduleDefinition] = [
    ScheduleDefinition(
        "A",
        "Receipts (Schedule A)",
        True,
        "Interest; pensions/annuities/periodic payments; miscellaneous receipts",
        "Internal transfers between matter-owned accounts",
    ),
    ScheduleDefinition(
        "B",
        "Property on Hand at Beginning (POH Beginning)",
        True,
        "Cash/cash equivalents and non-cash assets at period start (from I&A or prior Schedule E)",
        "Income and expenses during the period",
    ),
    ScheduleDefinition(
        "C",
        "Disbursements",
        True,
        "Living, medical, legal/professional, insurance, miscellaneous — incl. bank fees and professional fees unless broken out to ad-hoc P",
        "Internal transfers between matter-owned accounts",
    ),
    ScheduleDefinition(
        "E",
        "Property on Hand at End (POH End)",
        True,
        "Ending cash/cash equivalents and non-cash assets",
        "Income/expense items",
    ),
    ScheduleDefinition(
        "F",
        "Additional Property Received During the Period",
        True,
        "Property received during the accounting period",
        "Receipts already in Schedule A per firm convention",
    ),
    ScheduleDefinition(
        "D",
        "Losses on Sales During the Period",
        False,
        "Realized losses — ad-hoc sheet when required",
        "Unrealized losses",
    ),
    ScheduleDefinition(
        "G",
        "Distributions to Beneficiaries / Conservatee / Minor",
        False,
        "Distributions to non-matter accounts; personal needs allowance when broken out",
        "Internal matter-to-matter transfers",
    ),
    ScheduleDefinition(
        "I",
        "Net Income from Trade or Business",
        False,
        "Business income — ad-hoc when matter operates a business",
        "Wage income (typically Schedule A)",
    ),
    ScheduleDefinition(
        "K",
        "Change in Assets",
        False,
        "Non-trivial changes in character/holding of assets",
        "Routine internal restructuring between matter accounts",
    ),
    ScheduleDefinition(
        "L",
        "Net Loss from Trade or Business",
        False,
        "Business losses — ad-hoc sheet",
        "Non-business expenses (Schedule C)",
    ),
    ScheduleDefinition(
        "P",
        "Professional Fees",
        False,
        "Optional breakout from Schedule C when the firm uses a separate sheet",
        "Fees left in Schedule C when not broken out",
    ),
    ScheduleDefinition(
        "X",
        "Cash Reconciliation",
        False,
        "Separate reconciliation schedule when required",
        "Routine reconciliation on Bank Statement Transactions / Working Balance",
    ),
]


def schedules_prompt_block() -> str:
    lines = []
    for s in SCHEDULES:
        kind = "standard template sheet" if s.standard_in_template else "ad-hoc sheet (create when needed)"
        lines.append(
            f"- Schedule {s.letter} — {s.name} ({kind}). "
            f"Includes: {s.includes}. Excludes: {s.excludes}."
        )
    lines.append(
        "- Internal transfers between accounts both held in the name of the estate/trust/"
        "conservatorship → use schedule value \"internal_transfer\" (excluded from schedule "
        "totals; retained in audit trail)."
    )
    lines.append(
        "- Transfers to a non-matter account are categorized (often ad-hoc Schedule G or "
        "Schedule C / reimbursement depending on facts); use \"needs_review\" if ambiguous."
    )
    return "\n".join(lines)


def matter_type_notes(matter: MatterType) -> str:
    if matter == "conservatorship":
        return (
            "Conservatorship (Probate Code § 2620): Schedule C subcategories often match "
            "template defaults; personal needs allowance may appear under Schedule G when applicable; "
            "bond/court orders may appear in Working Balance header."
        )
    if matter == "probate_estate":
        return (
            "Probate estate (Probate Code § 1061): Schedule C labels vary per case (funeral, "
            "creditor claims, administration); I&A drives Schedule B; creditor payments often "
            "tracked within Schedule C."
        )
    return (
        "Trust administration (Probate Code § 16063, § 1064): principal vs income distinction "
        "may split subcategories within Schedules A/C or parallel sheets when significant."
    )
