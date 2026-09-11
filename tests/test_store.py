"""Tests for local usage-record storage and aggregation."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.store import Store


def record(
    record_id: str,
    *,
    minutes_ago: float,
    model: str = "deepseek/deepseek-v4.1-flash",
    tokens_in: int = 1000,
    tokens_out: int = 100,
    cost: float = 0.01,
    cache_cost: float = 0.002,
    status: str = "completed",
) -> dict[str, object]:
    created = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return {
        "id": record_id,
        "created_at": created.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "model": model,
        "provider": "test",
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_total": cost,
        "cost_input": cost * 0.7,
        "cost_output": cost * 0.2,
        "cost_cache": cache_cost,
        "duration_ms": 1500,
        "status": status,
        "entry_type": "api",
        "mode": "api",
        "trace_id": f"trace-{record_id}",
    }


class StoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self._tmp.name) / "test.db")

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def test_insert_is_idempotent(self) -> None:
        rows = [record("a", minutes_ago=5), record("b", minutes_ago=10)]
        self.assertEqual(self.store.insert_usage_records(rows), 2)
        self.assertEqual(self.store.insert_usage_records(rows), 0)
        self.assertEqual(self.store.usage_record_bounds()["count"], 2)

    def test_totals_aggregate_tokens_and_cost(self) -> None:
        self.store.insert_usage_records(
            [
                record("a", minutes_ago=5, tokens_in=1000, tokens_out=200, cost=0.5),
                record("b", minutes_ago=10, tokens_in=3000, tokens_out=100, cost=0.25),
            ]
        )
        totals = self.store.usage_totals("today")
        self.assertEqual(totals["requests"], 2)
        self.assertEqual(totals["tokens_in"], 4000)
        self.assertEqual(totals["tokens_out"], 300)
        self.assertEqual(totals["tokens_total"], 4300)
        self.assertAlmostEqual(totals["cost_total"], 0.75, places=6)
        self.assertAlmostEqual(totals["average_cost"], 0.375, places=6)
        self.assertEqual(totals["success_rate"], 100.0)

    def test_success_rate_and_failed_count(self) -> None:
        self.store.insert_usage_records(
            [
                record("ok", minutes_ago=1),
                record("bad", minutes_ago=2, status="failed"),
                record("bad2", minutes_ago=3, status="failed"),
                record("bad3", minutes_ago=4, status="failed"),
            ]
        )
        totals = self.store.usage_totals("today")
        self.assertEqual(totals["completed"], 1)
        self.assertEqual(totals["failed"], 3)
        self.assertAlmostEqual(totals["success_rate"], 25.0, places=3)

    def test_today_range_excludes_old_records(self) -> None:
        self.store.insert_usage_records(
            [
                record("recent", minutes_ago=5, cost=1.0),
                record("old", minutes_ago=60 * 24 * 3, cost=99.0),
            ]
        )
        self.assertEqual(self.store.usage_totals("today")["requests"], 1)
        self.assertEqual(self.store.usage_totals("all")["requests"], 2)

    def test_model_stats_group_and_sort_by_tokens(self) -> None:
        self.store.insert_usage_records(
            [
                record("a", minutes_ago=1, model="model-a", tokens_in=100, tokens_out=10),
                record("b", minutes_ago=2, model="model-a", tokens_in=900, tokens_out=10),
                record("c", minutes_ago=3, model="model-b", tokens_in=50, tokens_out=5),
            ]
        )
        models = self.store.model_stats("today")
        self.assertEqual([item["model"] for item in models], ["model-a", "model-b"])
        self.assertEqual(models[0]["requests"], 2)
        self.assertEqual(models[0]["tokens_total"], 1020)

    def test_records_page_filters_and_paginates(self) -> None:
        self.store.insert_usage_records(
            [
                record(f"a{i}", minutes_ago=i, model="model-a")
                for i in range(1, 8)
            ]
            + [record("b1", minutes_ago=50, model="model-b")]
        )
        page1, total = self.store.usage_records_page(page=1, page_size=3, range_key="today")
        self.assertEqual(total, 8)
        self.assertEqual(len(page1), 3)
        page3, _ = self.store.usage_records_page(page=3, page_size=3, range_key="today")
        self.assertEqual(len(page3), 2)
        filtered, filtered_total = self.store.usage_records_page(
            page=1,
            page_size=10,
            model="model-b",
            range_key="today",
        )
        self.assertEqual(filtered_total, 1)
        self.assertEqual(filtered[0]["model"], "model-b")
        newest_first = [row["created_ms"] for row in page1]
        self.assertEqual(newest_first, sorted(newest_first, reverse=True))

    def test_daily_stats_bucket_by_local_day(self) -> None:
        self.store.insert_usage_records(
            [record("a", minutes_ago=5), record("b", minutes_ago=10)]
        )
        daily = self.store.daily_stats("30d")
        self.assertEqual(len(daily), 1)
        self.assertEqual(daily[0]["requests"], 2)

    def test_list_models_is_sorted_and_unique(self) -> None:
        self.store.insert_usage_records(
            [
                record("a", minutes_ago=1, model="z-model"),
                record("b", minutes_ago=2, model="a-model"),
                record("c", minutes_ago=3, model="z-model"),
            ]
        )
        self.assertEqual(self.store.list_models(), ["a-model", "z-model"])

    def test_cache_hit_rate_uses_cache_read_over_input(self) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.store.insert_usage_buckets(
            [
                {
                    "model": "model-a",
                    "time_bucket": now,
                    "tokens_in": 1000,
                    "tokens_out": 10,
                    "cache_read_tokens": 900,
                    "cache_write_tokens": 50,
                    "cost_cache": 0.1,
                    "requests": 3,
                }
            ]
        )
        cache = self.store.cache_stats("today")
        self.assertEqual(cache["tokens_in"], 1000)
        self.assertEqual(cache["cache_read"], 900)
        self.assertEqual(cache["cache_miss"], 100)
        self.assertAlmostEqual(cache["hit_rate"], 90.0, places=6)
        self.assertEqual(cache["buckets"], 1)

    def test_cache_buckets_are_idempotent_per_model_bucket(self) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        bucket = {
            "model": "model-a",
            "time_bucket": now,
            "tokens_in": 500,
            "cache_read_tokens": 400,
            "requests": 1,
        }
        self.assertEqual(self.store.insert_usage_buckets([bucket]), 1)
        self.assertEqual(self.store.insert_usage_buckets([bucket]), 0)
        self.assertEqual(self.store.bucket_bounds()["count"], 1)
        # 同一桶更新为新值时不新增行
        self.store.insert_usage_buckets([{**bucket, "tokens_in": 800, "cache_read_tokens": 700}])
        cache = self.store.cache_stats("today")
        self.assertEqual(cache["tokens_in"], 800)
        self.assertAlmostEqual(cache["hit_rate"], 87.5, places=6)

    def test_model_cache_stats_keyed_by_model(self) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.store.insert_usage_buckets(
            [
                {
                    "model": "model-a",
                    "time_bucket": now,
                    "tokens_in": 1000,
                    "cache_read_tokens": 800,
                    "requests": 2,
                },
                {
                    "model": "model-b",
                    "time_bucket": now,
                    "tokens_in": 500,
                    "cache_read_tokens": 100,
                    "requests": 1,
                },
            ]
        )
        stats = self.store.model_cache_stats("today")
        self.assertAlmostEqual(stats["model-a"]["hit_rate"], 80.0, places=6)
        self.assertAlmostEqual(stats["model-b"]["hit_rate"], 20.0, places=6)

    def test_cache_stats_empty_range_is_zeroed(self) -> None:
        cache = self.store.cache_stats("today")
        self.assertEqual(cache["tokens_in"], 0)
        self.assertEqual(cache["hit_rate"], 0.0)
        self.assertEqual(cache["buckets"], 0)


if __name__ == "__main__":
    unittest.main()
