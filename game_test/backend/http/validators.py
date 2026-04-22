"""HTTP request parsing helpers."""

from typing import Any

from flask import request


def get_json_body() -> Any:
    return request.get_json(silent=True) or {}


def get_int_field(body: Any, field_name: str, default: int = 0) -> int:
    raw = body.get(field_name, default) if isinstance(body, dict) else default
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} 必须是整数")
