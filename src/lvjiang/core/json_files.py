"""独立 JSON 文件的可读输出；不用于签名、摘要、JSONL 或传输协议。"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any


def formatted_json(value: object, *, default: Callable[[Any], Any] | None = None) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=default) + "\n"


def formatted_json_bytes(value: object, *, default: Callable[[Any], Any] | None = None) -> bytes:
    return formatted_json(value, default=default).encode("utf-8")
