"""Launch GOAT Gauge in a dedicated Chrome application window."""

from __future__ import annotations

import os
import json
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from .server import GaugeService, start_server, stop_server
from .store import data_dir


def _log_path() -> Path:
    return data_dir() / "chrome-mode.log"


def log(message: str) -> None:
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except OSError:
        pass


def chrome_path() -> str:
    candidates = [
        os.environ.get("GOATGAUGE_CHROME_EXE", ""),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError("找不到 Google Chrome。可设置 GOATGAUGE_CHROME_EXE。")


CDP_PORT = int(os.environ.get("GOATGAUGE_CHROME_CDP_PORT", "9333"))
LOGIN_URL = "https://commandcode.ai/signin?returnTo=%2Fsettings%2Fusage"


def _profile_dir() -> Path:
    configured = os.environ.get("GOATGAUGE_CHROME_PROFILE", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return data_dir() / "chrome-profile"


def open_chrome(url: str) -> subprocess.Popen[bytes]:
    profile = _profile_dir()
    profile.mkdir(parents=True, exist_ok=True)
    args = [
        chrome_path(),
        f"--remote-debugging-port={CDP_PORT}",
        "--remote-allow-origins=*",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate",
        "--app=" + url,
        "--window-size=1280,820",
    ]
    log("opening " + url)
    return subprocess.Popen(args, close_fds=True)


def _cdp_alive() -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{CDP_PORT}/json/version",
            timeout=1.5,
        ) as response:
            return response.status == 200
    except Exception:  # noqa: BLE001
        return False


def _page_ws_url() -> str | None:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{CDP_PORT}/json/list",
            timeout=2,
        ) as response:
            targets = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return None
    for target in targets:
        if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
            return str(target["webSocketDebuggerUrl"])
    return None


def read_chrome_cookies() -> list[dict[str, object]] | None:
    """Read all commandcode.ai cookies from the dedicated Chrome profile."""
    if not _cdp_alive():
        return None
    ws_url = _page_ws_url()
    if not ws_url:
        return None
    try:
        import websocket  # type: ignore

        connection = websocket.create_connection(ws_url, timeout=3)
        try:
            connection.send(json.dumps({"id": 1, "method": "Network.getAllCookies"}))
            deadline = time.time() + 4
            while time.time() < deadline:
                message = json.loads(connection.recv())
                if message.get("id") != 1:
                    continue
                cookies = message.get("result", {}).get("cookies", [])
                return [
                    cookie
                    for cookie in cookies
                    if "commandcode.ai" in str(cookie.get("domain", ""))
                ]
        finally:
            connection.close()
    except Exception as exc:  # noqa: BLE001
        log(f"CDP cookie lookup failed: {type(exc).__name__}: {exc}")
    return None


def _cookie_watcher(
    service: GaugeService,
    stop_event: threading.Event,
) -> None:
    last_signature = ""
    while not stop_event.wait(2.0):
        try:
            cookies = read_chrome_cookies()
            if not cookies:
                continue
            auth_cookies = [
                cookie
                for cookie in cookies
                if any(
                    marker in str(cookie.get("name", ""))
                    for marker in ("session_token", "session_data")
                )
            ]
            if not auth_cookies:
                continue
            signature = "|".join(
                f"{cookie.get('name')}={cookie.get('value')}"
                for cookie in auth_cookies
            )
            if signature == last_signature:
                continue
            service.set_browser_session(auth_cookies)
            last_signature = signature
            log("captured Command Code browser session")
        except Exception as exc:  # noqa: BLE001
            log(f"cookie watcher iteration failed: {type(exc).__name__}: {exc}")


def main(*, host: str, port: int, demo: bool, open_browser: bool) -> None:
    service = GaugeService(demo=demo)
    bound_host, bound_port = start_server(
        host=host,
        port=port or 18927,
        service=service,
    )
    url = f"http://{bound_host}:{bound_port}/"
    service.start_background()
    service.set_open_browser_callback(lambda: open_chrome(LOGIN_URL))
    service.set_browser_session_reader(read_chrome_cookies)

    stop_event = threading.Event()
    watcher = threading.Thread(
        target=_cookie_watcher,
        args=(service, stop_event),
        daemon=True,
        name="goat-gauge-chrome-cookie",
    )
    watcher.start()

    chrome_process: subprocess.Popen[bytes] | None = None
    if open_browser:
        try:
            chrome_process = open_chrome(url)
            threading.Thread(
                target=_open_login_if_needed,
                args=(service,),
                daemon=True,
                name="goat-gauge-login-fallback",
            ).start()
        except Exception as exc:  # noqa: BLE001
            log(f"Chrome launch failed: {type(exc).__name__}: {exc}")
            raise

    log(f"server started at {url}")
    service.set_stop_callback(stop_event.set)
    try:
        stop_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        stop_server()
        service.stop()
        log("server stopped")


def _open_login_if_needed(service: GaugeService) -> None:
    """Wait briefly for an existing session before prompting for sign-in."""
    deadline = time.time() + 8
    while time.time() < deadline:
        if service.configured():
            return
        time.sleep(0.5)
    if not service.configured():
        log("no existing browser session; opening Command Code sign-in")
        open_chrome(LOGIN_URL)
