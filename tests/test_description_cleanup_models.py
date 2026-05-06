"""Unit tests: description cleanup JSON → Pydantic (no Gemini calls)."""

from __future__ import annotations

import json
import unittest

from app.models import ConfidenceLevel, DescriptionCleanupItem, DescriptionCleanupResult


class TestDescriptionCleanupModels(unittest.TestCase):
    def test_parse_cleanup_result(self) -> None:
        raw = {
            "cleanups": [
                {
                    "transactionId": "abc-1",
                    "cleanedDescription": "Grocery Store #12",
                    "confidence": "high",
                    "reasoning": None,
                },
                {
                    "transactionId": "abc-2",
                    "cleanedDescription": "ACH XFER",
                    "confidence": "low",
                    "reasoning": "ambiguous",
                },
            ]
        }
        res = DescriptionCleanupResult.model_validate(raw)
        self.assertEqual(len(res.cleanups), 2)
        self.assertEqual(res.cleanups[0].transaction_id, "abc-1")
        self.assertEqual(res.cleanups[0].confidence, ConfidenceLevel.high)
        self.assertEqual(res.cleanups[1].confidence, ConfidenceLevel.low)

    def test_item_json_roundtrip(self) -> None:
        item = DescriptionCleanupItem(
            transaction_id="t1",
            cleaned_description="Payee Name",
            confidence=ConfidenceLevel.medium,
            reasoning="test",
        )
        dumped = json.loads(item.model_dump_json(by_alias=True))
        self.assertEqual(dumped["transactionId"], "t1")
        self.assertEqual(dumped["cleanedDescription"], "Payee Name")


if __name__ == "__main__":
    unittest.main()
