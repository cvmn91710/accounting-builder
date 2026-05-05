"""Regression: sample Wells Fargo PDF yields rich text (transaction history in body, not only tiny tables)."""

from __future__ import annotations

import unittest
from pathlib import Path

from app.text_extract import extract_pdf

SAMPLE = (
    Path(__file__).resolve().parent.parent
    / "BARTOLOTTA STATEMENT WELLS FARGO APRIL 2022 (1).pdf"
)


@unittest.skipUnless(SAMPLE.is_file(), "Sample PDF not in repo root")
class TestWellsFargoSampleExtract(unittest.TestCase):
    def test_combined_text_has_transaction_history(self) -> None:
        doc = extract_pdf(SAMPLE)
        self.assertGreater(len(doc.combined_text), 8_000, "expected multi-page text")
        low = doc.combined_text.lower()
        self.assertIn("wells fargo", low)
        self.assertTrue(
            "transaction" in low or "deposits" in low or "withdrawals" in low,
            "expected transaction-related section in extracted text",
        )
        self.assertIn("edward jones", low)

    def test_tables_not_primary_for_this_statement(self) -> None:
        """Guardrail: junk tables must stay small vs full text (prompt orders FULL TEXT first)."""
        doc = extract_pdf(SAMPLE)
        self.assertGreater(len(doc.combined_text), len(doc.tables_summary or "") * 3)


if __name__ == "__main__":
    unittest.main()
