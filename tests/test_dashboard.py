"""Tests for dashboard aggregation fallbacks."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone

from app.server import GaugeService


def _record(record_id: str, *, cost: float) -> dict[str, object]:
    created = datetime.now(timezone.utc)
    return {
        "id": record_id,
        "created_at": created.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "model": "deepseek/deepseek-v4.1-flash",
        "provider": "test",
        "tokens_in": 1000,
        "tokens_out": 100,
        "cost_total": cost,
        "cost_input": cost * 0.7,
        "cost_output": cost * 0.2,
        "cost_cache": 0.0,
        "duration_ms": 1500,
        "status": "completed",
        "entry_type": "api",
        "mode": "api",
        "trace_id": f"trace-{record_id}",
    }


class UpstreamFallbackTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._previous_data = os.environ.get("GOATGAUGE_DATA")
        os.environ["GOATGAUGE_DATA"] = self._tmp.name
        self.service = GaugeService()

    def tearDown(self) -> None:
        self.service.store.close()
        if self._previous_data is None:
            os.environ.pop("GOATGAUGE_DATA", None)
        else:
            os.environ["GOATGAUGE_DATA"] = self._previous_data
        self._tmp.cleanup()

    def _set_snapshot_usage(self, **usage) -> None:
        with self.service._lock:
            self.service._snapshot = {"fetched_at": 1, "usage": usage}

    def test_empty_store_falls_back_to_billing_aggregate(self) -> None:
        self._set_snapshot_usage(
            total_count=2987,
            total_cost=48.8898,
            average_cost=0.0164,
            success_rate=100.0,
            completed_count=2987,
            failed_count=0,
            tokens_in=909_538_799,
            tokens_out=2_702_492,
            period_basis="billing-period",
        )
        totals = self.service.dashboard("today")["totals"]
        self.assertEqual(totals["source"], "upstream")
        self.assertEqual(totals["requests"], 2987)
        self.assertEqual(totals["completed"], 2987)
        self.assertAlmostEqual(totals["cost_total"], 48.8898, places=4)
        self.assertEqual(totals["tokens_total"], 909_538_799 + 2_702_492)
        self.assertEqual(totals["period"], "billing-period")
        self.assertEqual(totals["detail_records"], 0)

    def test_local_records_win_over_the_aggregate(self) -> None:
        self.service.store.insert_usage_records([_record("a", cost=0.5)])
        self._set_snapshot_usage(total_count=2987, total_cost=48.8898)
        totals = self.service.dashboard("today")["totals"]
        self.assertEqual(totals["source"], "records")
        self.assertEqual(totals["requests"], 1)
        self.assertAlmostEqual(totals["cost_total"], 0.5, places=6)

    def test_no_aggregate_falls_back_cleanly(self) -> None:
        with self.service._lock:
            self.service._snapshot = {"fetched_at": 1}
        totals = self.service.dashboard("today")["totals"]
        self.assertEqual(totals["source"], "records")
        self.assertEqual(totals["requests"], 0)
        self.assertEqual(totals["cost_total"], 0.0)


if __name__ == "__main__":
    unittest.main()
