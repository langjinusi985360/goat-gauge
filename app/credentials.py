"""Credential detection and Windows DPAPI storage."""

from __future__ import annotations

import base64
import ctypes
import json
import os
import sqlite3
import stat
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any, Iterable


KEY_ENV_NAMES = ("COMMAND_CODE_API_KEY", "COMMANDCODE_API_KEY")
KEY_NAMES = {
    "apikey",
    "api_key",
    "commandcodeapikey",
    "command_code_api_key",
    "key",
    "openaiapikey",
    "openai_api_key",
    "token",
}

CC_SWITCH_DB_ENV = "CC_SWITCH_DB"
CC_SWITCH_DB_NAME = "cc-switch.db"
COMMAND_CODE_MARKERS = ("commandcode", "command code")


class CredentialError(RuntimeError):
    """Raised when a credential cannot be stored or decoded."""


def validate_key(value: str) -> str:
    key = (value or "").strip()
    if len(key) < 8:
        raise CredentialError("API Key 长度不足，请确认复制完整。")
    try:
        key.encode("ascii")
    except UnicodeEncodeError as exc:
        raise CredentialError("API Key 只能包含 ASCII 字符。") from exc
    if any(ch.isspace() for ch in key):
        raise CredentialError("API Key 不能包含空格或换行。")
    return key


def mask_key(value: str) -> str:
    key = (value or "").strip()
    if not key:
        return "未配置"
    prefix = ""
    for candidate in ("cc-", "sk-", "user-", "key-"):
        if key.lower().startswith(candidate):
            prefix = key[: len(candidate)]
            break
    tail = key[-4:] if len(key) >= 4 else ""
    return f"{prefix}*****{tail}"


def _walk_json(value: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = path + (str(key),)
            if isinstance(child, str):
                normalized = str(key).replace("-", "").replace("_", "").lower()
                if normalized in KEY_NAMES and child.strip():
                    yield child_path, child.strip()
            else:
                yield from _walk_json(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_json(child, path + (str(index),))


def _read_key_from_file(path: Path) -> tuple[str, str] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    for _, candidate in _walk_json(data):
        try:
            return validate_key(candidate), str(path)
        except CredentialError:
            continue
    return None


def cc_switch_db_path() -> Path:
    configured = os.environ.get(CC_SWITCH_DB_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".cc-switch" / CC_SWITCH_DB_NAME


def _is_command_code_provider(*fields: str) -> bool:
    blob = "\n".join(fields).lower()
    return any(marker in blob for marker in COMMAND_CODE_MARKERS)


def _read_cc_switch_key() -> tuple[str, str] | None:
    """Reuse the Command Code API key that CC Switch already keeps on disk.

    Only providers that clearly point at Command Code are considered, so keys
    belonging to other vendors are never sent anywhere.
    """
    path = cc_switch_db_path()
    if not path.is_file():
        return None

    try:
        connection = sqlite3.connect(
            f"{path.as_uri()}?mode=ro",
            uri=True,
            timeout=1.5,
        )
    except sqlite3.Error:
        return None

    try:
        rows = connection.execute(
            "SELECT name, website_url, settings_config FROM providers"
        ).fetchall()
    except sqlite3.Error:
        return None
    finally:
        connection.close()

    best: tuple[int, str, str] | None = None
    for name, website, raw in rows:
        name = str(name or "")
        website = str(website or "")
        raw = str(raw or "")
        if not _is_command_code_provider(name, website, raw):
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for _, candidate in _walk_json(payload):
            try:
                key = validate_key(candidate)
            except CredentialError:
                continue
            # A provider whose base_url points at api.commandcode.ai is the
            # most trustworthy match; name/website matches are the fallback.
            priority = 0 if "api.commandcode.ai" in raw.lower() else 1
            label = f"CC Switch（{name or 'Command Code'}）"
            if best is None or priority < best[0]:
                best = (priority, key, label)
            break
    if best is None:
        return None
    return best[1], best[2]


def detect_source() -> tuple[str | None, str | None]:
    """Return (key, source label) without exposing the key."""
    for name in KEY_ENV_NAMES:
        value = os.environ.get(name, "").strip()
        if value:
            try:
                return validate_key(value), f"环境变量 {name}"
            except CredentialError:
                continue

    home = Path.home()
    candidates = (
        home / ".commandcode" / "auth.json",
        home / ".commandcode" / "config.json",
        home / "AppData" / "Roaming" / "CommandCode" / "auth.json",
        home / "AppData" / "Roaming" / "CommandCode" / "config.json",
    )
    for path in candidates:
        found = _read_key_from_file(path)
        if found:
            key, source = found
            return key, source

    found = _read_cc_switch_key()
    if found:
        key, source = found
        return key, source
    return None, None


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _protect_windows(data: bytes) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buffer_in = ctypes.create_string_buffer(data, len(data))
    blob_in = _DataBlob(
        len(data),
        ctypes.cast(buffer_in, ctypes.POINTER(ctypes.c_char)),
    )
    blob_out = _DataBlob()
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    if not crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        "GOAT Gauge credential",
        None,
        None,
        None,
        0,
        ctypes.byref(blob_out),
    ):
        raise CredentialError("Windows DPAPI 加密失败。")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def _unprotect_windows(data: bytes) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buffer_in = ctypes.create_string_buffer(data, len(data))
    blob_in = _DataBlob(
        len(data),
        ctypes.cast(buffer_in, ctypes.POINTER(ctypes.c_char)),
    )
    blob_out = _DataBlob()
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    if not crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(blob_out),
    ):
        raise CredentialError("Windows DPAPI 解密失败。")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def _encrypt_bytes(raw: bytes) -> bytes:
    if os.name == "nt":
        return b"DPAPI1\0" + _protect_windows(raw)
    return b"PLAIN1\0" + base64.b64encode(raw)


def _decrypt_bytes(payload: bytes) -> bytes | None:
    if payload.startswith(b"DPAPI1\0"):
        if os.name != "nt":
            return None
        return _unprotect_windows(payload[7:])
    if payload.startswith(b"PLAIN1\0"):
        return base64.b64decode(payload[7:])
    return None


class _EncryptedFile:
    """Small DPAPI-encrypted single-file store."""

    filename = "secret.bin"

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / self.filename

    def write(self, raw: bytes) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = _encrypt_bytes(raw)
        temp = self.path.with_suffix(".tmp")
        temp.write_bytes(payload)
        os.replace(temp, self.path)
        try:
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def read(self) -> bytes | None:
        try:
            payload = self.path.read_bytes()
        except OSError:
            return None
        try:
            return _decrypt_bytes(payload)
        except (CredentialError, UnicodeError, ValueError):
            return None

    def delete(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


class CredentialStore(_EncryptedFile):
    """Persist one API key, encrypted with DPAPI on Windows."""

    filename = "credential.bin"

    def save(self, key: str) -> None:
        self.write(validate_key(key).encode("utf-8"))

    def load(self) -> str | None:
        raw = self.read()
        if raw is None:
            return None
        try:
            return validate_key(raw.decode("utf-8", errors="strict"))
        except (CredentialError, UnicodeError):
            return None


class BrowserSessionStore(_EncryptedFile):
    """Persist the captured commandcode.ai browser session (cookies)."""

    filename = "browser-session.bin"

    def save(self, cookie_header: str, user_agent: str) -> None:
        if not cookie_header.strip():
            return
        payload = json.dumps(
            {
                "cookie": cookie_header,
                "user_agent": user_agent or "Mozilla/5.0",
                "saved_at": int(time.time()),
            }
        )
        self.write(payload.encode("utf-8"))

    def load(self) -> tuple[str, str] | None:
        raw = self.read()
        if raw is None:
            return None
        try:
            data = json.loads(raw.decode("utf-8", errors="strict"))
        except (UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        cookie = str(data.get("cookie") or "").strip()
        if not cookie:
            return None
        return cookie, str(data.get("user_agent") or "Mozilla/5.0")
