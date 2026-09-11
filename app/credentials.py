"""Credential detection and Windows DPAPI storage."""

from __future__ import annotations

import base64
import ctypes
import json
import os
import stat
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
    "token",
}


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


class CredentialStore:
    """Persist one API key, encrypted with DPAPI on Windows."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "credential.bin"

    def save(self, key: str) -> None:
        key = validate_key(key)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        raw = key.encode("utf-8")
        if os.name == "nt":
            payload = b"DPAPI1\0" + _protect_windows(raw)
        else:
            payload = b"PLAIN1\0" + base64.b64encode(raw)
        temp = self.path.with_suffix(".tmp")
        temp.write_bytes(payload)
        os.replace(temp, self.path)
        try:
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def load(self) -> str | None:
        try:
            payload = self.path.read_bytes()
        except OSError:
            return None
        try:
            if payload.startswith(b"DPAPI1\0"):
                if os.name != "nt":
                    return None
                raw = _unprotect_windows(payload[7:])
            elif payload.startswith(b"PLAIN1\0"):
                raw = base64.b64decode(payload[7:])
            else:
                return None
            return validate_key(raw.decode("utf-8", errors="strict"))
        except (CredentialError, UnicodeError, ValueError):
            return None

    def delete(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
