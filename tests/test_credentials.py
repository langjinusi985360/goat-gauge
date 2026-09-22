"""Tests for credential reuse and browser-session persistence."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.credentials import BrowserSessionStore, CredentialStore, _read_cc_switch_key
from app.server import GaugeService


def _write_cc_switch_db(path: Path, providers: list[dict[str, object]]) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE providers (name TEXT, website_url TEXT, settings_config TEXT)"
    )
    connection.executemany(
        "INSERT INTO providers (name, website_url, settings_config) VALUES (?, ?, ?)",
        [
            (
                provider.get("name"),
                provider.get("website_url"),
                json.dumps(provider.get("settings_config")),
            )
            for provider in providers
        ],
    )
    connection.commit()
    connection.close()


class CredentialStoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_api_key_round_trip(self) -> None:
        store = CredentialStore(self.data_dir)
        store.save("user_test_key_1234567890")
        self.assertEqual(store.load(), "user_test_key_1234567890")
        store.delete()
        self.assertIsNone(store.load())

    def test_browser_session_round_trip(self) -> None:
        store = BrowserSessionStore(self.data_dir)
        self.assertIsNone(store.load())
        store.save("session=abc; other=1", "Mozilla/5.0 Test")
        self.assertEqual(
            store.load(), ("session=abc; other=1", "Mozilla/5.0 Test")
        )
        store.delete()
        self.assertIsNone(store.load())

    def test_browser_session_ignores_empty_cookie(self) -> None:
        store = BrowserSessionStore(self.data_dir)
        store.save("   ", "Mozilla/5.0")
        self.assertIsNone(store.load())


class CcSwitchTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "cc-switch.db"
        self._previous = os.environ.get("CC_SWITCH_DB")

    def tearDown(self) -> None:
        if self._previous is None:
            os.environ.pop("CC_SWITCH_DB", None)
        else:
            os.environ["CC_SWITCH_DB"] = self._previous
        self._tmp.cleanup()

    def test_missing_database_is_ignored(self) -> None:
        os.environ["CC_SWITCH_DB"] = str(self.db)
        self.assertIsNone(_read_cc_switch_key())

    def test_command_code_provider_key_is_reused(self) -> None:
        _write_cc_switch_db(
            self.db,
            [
                {
                    "name": "Command Code",
                    "website_url": "https://commandcode.ai/zh",
                    "settings_config": {
                        "auth": {"OPENAI_API_KEY": "user_cc_key_1234567890"},
                        "config": 'base_url = "https://api.commandcode.ai/provider/v1"',
                    },
                }
            ],
        )
        os.environ["CC_SWITCH_DB"] = str(self.db)
        self.assertEqual(
            _read_cc_switch_key(), ("user_cc_key_1234567890", "CC Switch（Command Code）")
        )

    def test_other_vendor_keys_are_never_used(self) -> None:
        _write_cc_switch_db(
            self.db,
            [
                {
                    "name": "DeepSeek",
                    "website_url": "https://platform.deepseek.com",
                    "settings_config": {
                        "auth": {"OPENAI_API_KEY": "sk-deepseek-key-123456"},
                        "config": 'base_url = "https://api.deepseek.com/v1"',
                    },
                },
                {
                    "name": "OpenAI",
                    "website_url": "https://platform.openai.com",
                    "settings_config": {
                        "auth": {"OPENAI_API_KEY": "sk-openai-key-123456"}
                    },
                },
            ],
        )
        os.environ["CC_SWITCH_DB"] = str(self.db)
        self.assertIsNone(_read_cc_switch_key())


class BrowserSessionRestoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._previous_data = os.environ.get("GOATGAUGE_DATA")
        self._previous_cc = os.environ.get("CC_SWITCH_DB")
        self._previous_env_key = os.environ.get("COMMAND_CODE_API_KEY")
        os.environ["GOATGAUGE_DATA"] = self._tmp.name
        # Keep the test hermetic: no CC Switch database, no ambient API key.
        os.environ["CC_SWITCH_DB"] = str(Path(self._tmp.name) / "absent.db")
        os.environ["COMMAND_CODE_API_KEY"] = ""

    def tearDown(self) -> None:
        for name, previous in (
            ("GOATGAUGE_DATA", self._previous_data),
            ("CC_SWITCH_DB", self._previous_cc),
            ("COMMAND_CODE_API_KEY", self._previous_env_key),
        ):
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous
        self._tmp.cleanup()

    def test_saved_session_survives_restart(self) -> None:
        store = BrowserSessionStore(Path(self._tmp.name))
        store.save("session=abc; other=1", "Mozilla/5.0 Test")

        service = GaugeService()
        try:
            self.assertTrue(service.configured())
            meta = service.state()["meta"]
            self.assertEqual(meta["credential_mode"], "browser")
            self.assertEqual(meta["key_source"], "本地加密会话")
        finally:
            service.store.close()

    def test_clear_key_removes_saved_session(self) -> None:
        store = BrowserSessionStore(Path(self._tmp.name))
        store.save("session=abc; other=1", "Mozilla/5.0 Test")

        service = GaugeService()
        try:
            service.clear_key()
            self.assertFalse(service.configured())
            self.assertIsNone(BrowserSessionStore(Path(self._tmp.name)).load())
        finally:
            service.store.close()


if __name__ == "__main__":
    unittest.main()
