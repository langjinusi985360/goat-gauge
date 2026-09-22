"""Tests for the dedicated Chrome profile helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.chrome_mode import _preserve_session_cookies


class PreserveSessionCookiesTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.profile = Path(self._tmp.name)
        (self.profile / "Default").mkdir(parents=True, exist_ok=True)
        self.preferences = self.profile / "Default" / "Preferences"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _read(self) -> dict:
        return json.loads(self.preferences.read_text(encoding="utf-8"))

    def test_enables_restore_on_startup(self) -> None:
        self.preferences.write_text(json.dumps({"session": {"startup_urls": []}}))
        _preserve_session_cookies(self.profile)
        self.assertEqual(self._read()["session"]["restore_on_startup"], 1)
        self.assertEqual(self._read()["session"]["startup_urls"], [])

    def test_creates_preferences_when_missing(self) -> None:
        _preserve_session_cookies(self.profile)
        self.assertEqual(self._read()["session"]["restore_on_startup"], 1)

    def test_ignores_corrupt_preferences(self) -> None:
        self.preferences.write_text("{not json")
        _preserve_session_cookies(self.profile)
        self.assertEqual(self._read()["session"]["restore_on_startup"], 1)

    def test_is_idempotent(self) -> None:
        _preserve_session_cookies(self.profile)
        first = self.preferences.read_text(encoding="utf-8")
        _preserve_session_cookies(self.profile)
        self.assertEqual(self.preferences.read_text(encoding="utf-8"), first)


if __name__ == "__main__":
    unittest.main()
