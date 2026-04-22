"""HTTP response serialization helpers."""

from typing import Any

from flask import jsonify


def _json_ok(result: dict[str, Any], fallback_status: int = 400):
    return jsonify(result), 200 if result.get("ok") else fallback_status
