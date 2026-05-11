"""HTTP-facing inventory façade."""

from typing import Any

from game_test.backend.domain.inventory.backpack import get_backpack_snapshot
from game_test.backend.runtime import get_session
from game_test.backend.runtime.actions import send_action


def get_backpack() -> dict[str, Any]:
    return {"ok": True, "items": get_backpack_snapshot()}


def refresh_backpack() -> dict[str, Any]:
    session = get_session()
    items = get_backpack_snapshot()
    session.notify_backpack_update()
    return {"ok": True, "items": items, "count": len(items)}


def use_item(payload: dict[str, Any]) -> dict[str, Any]:
    return send_action("item.use", payload)


def drop_item(payload: dict[str, Any]) -> dict[str, Any]:
    return send_action("item.drop", payload)


def decompose_item(payload: dict[str, Any]) -> dict[str, Any]:
    return send_action("item.decompose", payload)


def synthesize_item(payload: dict[str, Any]) -> dict[str, Any]:
    return send_action("item.synthesize", payload)


def exchange_wuling() -> dict[str, Any]:
    return send_action("item.exchange_wuling", {})


def buy_item(payload: dict[str, Any]) -> dict[str, Any]:
    return send_action("item.buy", payload)
