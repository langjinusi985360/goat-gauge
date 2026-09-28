"""Tests for proxy-aware HTTP routing.

The dashboard used to freeze whenever a local proxy client (Clash, mihomo)
started after GOAT Gauge, because ``urllib`` caches its default opener and
therefore keeps the proxy settings captured at first use.
"""

from __future__ import annotations

import unittest
import urllib.error
import urllib.request
from unittest import mock

from app.commandcode_api import (
    _read_response,
    _route_attempts,
    _system_proxies,
)


class SystemProxyTestCase(unittest.TestCase):
    def test_empty_values_are_dropped(self) -> None:
        with mock.patch(
            "app.commandcode_api.urllib.request.getproxies",
            return_value={"http": "http://127.0.0.1:7897", "https": "", "ftp": ""},
        ):
            self.assertEqual(_system_proxies(), {"http": "http://127.0.0.1:7897"})

    def test_registry_errors_are_tolerated(self) -> None:
        with mock.patch(
            "app.commandcode_api.urllib.request.getproxies",
            side_effect=OSError("registry unavailable"),
        ):
            self.assertEqual(_system_proxies(), {})


class RouteAttemptTestCase(unittest.TestCase):
    def test_direct_fallback_is_appended_for_proxied_hosts(self) -> None:
        proxy = {"https": "http://127.0.0.1:7897"}
        with mock.patch("app.commandcode_api._system_proxies", return_value=proxy):
            self.assertEqual(_route_attempts(), [proxy, {}])

    def test_direct_only_when_no_proxy_is_configured(self) -> None:
        with mock.patch("app.commandcode_api._system_proxies", return_value={}):
            self.assertEqual(_route_attempts(), [{}])


class ReadResponseTestCase(unittest.TestCase):
    def _patch(self, behaviour):
        """Patch build_opener and record the proxies of each attempt."""
        calls: list[dict[str, str]] = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def read(self):
                return b'{"ok": true}'

        def fake_build_opener(handler):
            calls.append(dict(handler.proxies))

            class FakeOpener:
                def open(self, request, timeout=None):  # noqa: ARG002
                    return behaviour(calls[-1], FakeResponse(), request)

            return FakeOpener()

        return calls, mock.patch(
            "urllib.request.build_opener", side_effect=fake_build_opener
        )

    def test_network_failure_retries_the_other_route(self) -> None:
        def behaviour(proxies, response, request):  # noqa: ARG001
            if proxies:
                raise urllib.error.URLError("timed out")
            return response

        calls, patcher = self._patch(behaviour)
        proxy = {"https": "http://127.0.0.1:7897"}
        with mock.patch("app.commandcode_api._system_proxies", return_value=proxy):
            with patcher:
                payload = _read_response(
                    urllib.request.Request("https://api.commandcode.ai/alpha/whoami"),
                    timeout=5,
                )
        self.assertEqual(payload, b'{"ok": true}')
        self.assertEqual(calls, [proxy, {}])

    def test_proxy_is_used_first_when_available(self) -> None:
        def behaviour(proxies, response, request):  # noqa: ARG001
            if not proxies:
                raise AssertionError("direct route should not be reached")
            return response

        calls, patcher = self._patch(behaviour)
        proxy = {"https": "http://127.0.0.1:7897"}
        with mock.patch("app.commandcode_api._system_proxies", return_value=proxy):
            with patcher:
                _read_response(
                    urllib.request.Request("https://api.commandcode.ai/alpha/whoami"),
                    timeout=5,
                )
        self.assertEqual(calls, [proxy])

    def test_http_errors_are_not_retried(self) -> None:
        def behaviour(proxies, response, request):  # noqa: ARG001
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

        calls, patcher = self._patch(behaviour)
        proxy = {"https": "http://127.0.0.1:7897"}
        with mock.patch("app.commandcode_api._system_proxies", return_value=proxy):
            with patcher:
                with self.assertRaises(urllib.error.HTTPError):
                    _read_response(
                        urllib.request.Request(
                            "https://api.commandcode.ai/alpha/whoami"
                        ),
                        timeout=5,
                    )
        self.assertEqual(calls, [proxy])


if __name__ == "__main__":
    unittest.main()
