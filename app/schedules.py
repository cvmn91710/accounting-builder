"""California probate schedule definitions and matter-type notes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MatterType = Literal["conservatorship", "probate_estate", "trust_administration"]

SCHEDULE_LETTERS = ("A", "B", "C", "D", "E", "F", "G", "H", "I")


@dataclass(frozen=True)
class ScheduleDefinition:
    letter: str
    name: str
    includes: str
    excludes: str


SCHEDULES: list[ScheduleDefinition] = [
    ScheduleDefinition(
        "A",
        "Property on Hand at Beginning / Inventory",
        "Opening account balances, securities held, real property",
        "Income earned during period",
    ),
    ScheduleDefinition(
        "B",
        "Receipts",
        "Interest, dividends, rental income, refunds, sale proceeds (principal portion)",
        "Internal transfers, gains on sale",
    ),
    ScheduleDefinition(
        "C",
        "Gains on Sales",
        "Realized gains on sold securities or property",
        "Unrealized gains, dividend reinvestments",
    ),
    ScheduleDefinition(
        "D",
        "Disbursements",
        "Bills paid, taxes, insurance, repairs, professional fees, bank fees",
        "Distributions to beneficiaries, fiduciary compensation",
    ),
    ScheduleDefinition(
        "E",
        "Losses on Sales",
        "Realized losses on sold securities or property",
        "Unrealized losses",
    ),
    ScheduleDefinition(
        "F",
        "Distributions",
        "Payments to beneficiaries, conservatee personal needs allowance",
        "Fiduciary compensation, expenses",
    ),
    ScheduleDefinition(
        "G",
        "Property on Hand at End",
        "Ending account balances, securities held, real property",
        "Income/expenses",
    ),
    ScheduleDefinition(
        "H",
        "Liabilities",
        "Mortgages, loans, unpaid bills as of period end",
        "Paid disbursements",
    ),
    ScheduleDefinition(
        "I",
        "Fiduciary Compensation",
        "Trustee fees, conservator fees, attorney fees",
        "Other professional fees → D",
    ),
]


def schedules_prompt_block() -> str:
    lines = []
    for s in SCHEDULES:
        lines.append(
            f"- Schedule {s.letter} — {s.name}. Includes: {s.includes}. Excludes: {s.excludes}."
        )
    return "\n".join(lines)


def matter_type_notes(matter: MatterType) -> str:
    if matter == "conservatorship":
        return (
            "Conservatorship: personal needs allowance may map to Schedule F; "
            "bond and court order references may apply to Schedule D."
        )
    if matter == "probate_estate":
        return (
            "Probate estate: inventory & appraisal cross-refs; creditor claim "
            "payments may be tracked within Schedule D per firm convention."
        )
    return (
        "Trust administration: principal vs income distinction where required; "
        "trustee compensation may split across principal/income per firm template."
    )
