"""Tests for currency settings persistence and validation."""

from __future__ import annotations

import os
import tempfile
import unittest

from app.server import GaugeService


class CurrencySettingTestCase(unittest.TestCase):
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

    def test_defaults_use_usd(self) -> None:
        settings = self.service.store.get_settings()
        self.assertEqual(settings["currency"], "USD")
        self.assertAlmostEqual(settings["usd_to_cny"], 7.2, places=4)

    def test_currency_switch_is_normalized_and_persisted(self) -> None:
        settings = self.service.save_settings({"currency": "cny"})
        self.assertEqual(settings["currency"], "CNY")
        self.assertEqual(self.service.store.get_settings()["currency"], "CNY")

    def test_unknown_currency_is_ignored(self) -> None:
        self.service.save_settings({"currency": "EUR"})
        self.assertEqual(self.service.store.get_settings()["currency"], "USD")

    def test_rate_range_is_validated(self) -> None:
        self.service.save_settings({"usd_to_cny": 7.35})
        self.assertAlmostEqual(
            self.service.store.get_settings()["usd_to_cny"], 7.35, places=4
        )
        for invalid in (0.01, 25, "abc", None):
            self.service.save_settings({"usd_to_cny": invalid})
            self.assertAlmostEqual(
                self.service.store.get_settings()["usd_to_cny"], 7.35, places=4
            )


if __name__ == "__main__":
    unittest.main()
