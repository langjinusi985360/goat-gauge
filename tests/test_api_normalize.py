"""Tests for CommandCode API response normalization."""

from __future__ import annotations

import unittest

from app.commandcode_api import (
    _normalize_chart_bucket,
    _epoch_ms,
    _normalize_usage,
    _normalize_usage_record,
    _normalize_window,
    plan_info,
)


class NormalizeTestCase(unittest.TestCase):
    def test_plan_info_matches_longest_prefix(self) -> None:
        self.assertEqual(plan_info("individual-goat"), ("GOAT", 70))
        self.assertEqual(plan_info("individual-pro-v1"), ("Pro", 80))
        self.assertEqual(plan_info("individual-go"), ("Go", 10))
        self.assertIsNone(plan_info("unknown-plan"))

    def test_normalize_window_computes_remaining(self) -> None:
        window = _normalize_window(
            {"used": 3, "cap": 12, "resetAt": 1_800_000_000_000},
            kind="five_hour",
            label="5 小时",
            limited=True,
        )
        self.assertTrue(window.available)
        self.assertEqual(window.remaining, 9.0)
        self.assertEqual(window.remaining_pct, 75.0)
        self.assertFalse(window.exceeded)

    def test_normalize_window_flags_exceeded(self) -> None:
        window = _normalize_window(
            {"used": 12, "cap": 12},
            kind="weekly",
            label="本周",
            limited=True,
        )
        self.assertTrue(window.exceeded)
        self.assertEqual(window.remaining, 0.0)

    def test_normalize_window_marks_unavailable_without_data(self) -> None:
        window = _normalize_window(None, kind="monthly", label="本月", limited=False)
        self.assertFalse(window.available)
        self.assertIn("不受此窗口限制", window.note)

    def test_normalize_usage_record_flattens_meta(self) -> None:
        normalized = _normalize_usage_record(
            {
                "id": "abc",
                "createdAt": "2026-09-11T13:22:09.174Z",
                "tokensIn": "529383",
                "tokensOut": "353",
                "durationTotal": "4164",
                "status": "completed",
                "meta": {
                    "totalCost": 0.00200325,
                    "inputCost": 0.00020745,
                    "outputCost": 0.0002118,
                    "cacheCost": 0.001584,
                    "model": "deepseek/deepseek-v4.1-flash",
                    "traceId": "trace",
                },
            }
        )
        self.assertIsNotNone(normalized)
        assert normalized is not None
        self.assertEqual(normalized["id"], "abc")
        self.assertEqual(normalized["model"], "deepseek/deepseek-v4.1-flash")
        self.assertEqual(normalized["tokens_in"], 529383)
        self.assertAlmostEqual(normalized["cost_total"], 0.00200325, places=9)
        self.assertEqual(normalized["status"], "completed")

    def test_normalize_usage_record_rejects_incomplete_rows(self) -> None:
        self.assertIsNone(_normalize_usage_record({"id": "only-id"}))
        self.assertIsNone(_normalize_usage_record({}))

    def test_normalize_usage_scales_fractional_success_rate(self) -> None:
        usage = _normalize_usage({"data": {"totalCount": 5, "successRate": 0.98}})
        self.assertEqual(usage["total_count"], 5)
        self.assertAlmostEqual(usage["success_rate"], 98.0, places=6)

    def test_epoch_ms_accepts_seconds_and_iso(self) -> None:
        self.assertEqual(_epoch_ms(1_800_000_000), 1_800_000_000_000)
        self.assertEqual(_epoch_ms(1_800_000_000_000), 1_800_000_000_000)
        self.assertGreater(_epoch_ms("2026-09-11T13:22:09.174Z"), 1_700_000_000_000)
        self.assertEqual(_epoch_ms("nonsense"), 0)

    def test_normalize_chart_bucket_reads_cache_tokens(self) -> None:
        bucket = _normalize_chart_bucket(
            {
                "model": "deepseek/deepseek-v4.1-flash",
                "provider": "vercel-ai-gateway",
                "timeBucket": "2026-09-11 13:20:00",
                "requests": 25,
                "totalCost": 0.29,
                "cacheCost": 0.037,
                "cacheSavings": 1.84,
                "tokensIn": 14_175_079,
                "tokensOut": 17_238,
                "tokensTotal": 14_192_317,
                "cacheReadInputTokens": 12_556_672,
                "cacheCreationInputTokens": 0,
            }
        )
        self.assertIsNotNone(bucket)
        assert bucket is not None
        self.assertEqual(bucket["model"], "deepseek/deepseek-v4.1-flash")
        self.assertEqual(bucket["tokens_in"], 14_175_079)
        self.assertEqual(bucket["cache_read_tokens"], 12_556_672)
        self.assertEqual(bucket["cache_write_tokens"], 0)
        self.assertAlmostEqual(bucket["cost_cache"], 0.037, places=9)

    def test_normalize_chart_bucket_rejects_incomplete_rows(self) -> None:
        self.assertIsNone(_normalize_chart_bucket({"model": "only-model"}))
        self.assertIsNone(_normalize_chart_bucket({}))


if __name__ == "__main__":
    unittest.main()
