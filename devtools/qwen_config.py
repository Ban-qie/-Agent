"""Explicit server-side Qwen configuration; importing this module reads no key."""
from __future__ import annotations

import os
import sys

from devtools.run_local import configure_offline

MODEL_ID = "global-qwen-qwen-flash"
API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def read_user_key() -> str:
    key = os.environ.get("qwen-api-key", "").strip()
    if not key and sys.platform == "win32":
        # A newly created user variable may not be inherited by this terminal.
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as handle:
                value, kind = winreg.QueryValueEx(handle, "qwen-api-key")
                if kind == winreg.REG_SZ and isinstance(value, str):
                    key = value.strip()
        except FileNotFoundError:
            pass
    if not key:
        raise RuntimeError("qwen-api-key is unavailable; configure the local user environment")
    return key


def configure_qwen(key: str) -> None:
    """Call before importing model_registry/app. Never launch a web server here."""
    if not isinstance(key, str) or not key.strip():
        raise ValueError("Qwen credential is empty")
    configure_offline()
    os.environ.update({
        "QWEN_ENABLED": "true",
        "QWEN_ENDPOINT": "openai",
        "QWEN_API_KEY": key.strip(),
        "QWEN_API_BASE": API_BASE,
        "QWEN_API_VERSION": "",
        "QWEN_MODELS": "qwen-flash",
    })
