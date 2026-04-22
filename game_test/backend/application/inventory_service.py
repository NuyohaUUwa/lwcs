"""HTTP-facing inventory façade."""

from typing import Any

from game_test.backend.domain.inventory.backpack import get_backpack_snapshot
from game_test.backend.runtime import get_session
from game_test.backend.runtime.actions import send_action

_JOB_PROTECTED_MAP = {
    "侠客": ["侠士战甲", "侠士头盔"],
    "刺客": ["刺客战甲", "刺客头盔"],
    "术士": ["术士战甲", "术士头盔"],
}


def _get_role_protected_items() -> list[str]:
    """Derive protected item names from current session role job (backend-side, not from frontend DOM)."""
    session = get_session()
    with session._lock:
        role = session.current_role
    if not role:
        return []
    job = str(role.role_job or "")
    return _JOB_PROTECTED_MAP.get(job, [])


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


def decompose_all_items(payload: dict[str, Any]) -> dict[str, Any]:
    protected_items = payload.get("protected_items")
    if protected_items is None:
        protected_items = _get_role_protected_items()
    return send_action("item.decompose_all", {"protected_items": protected_items})


def exchange_wuling() -> dict[str, Any]:
    return send_action("item.exchange_wuling", {})


def buy_item(payload: dict[str, Any]) -> dict[str, Any]:
    return send_action("item.buy", payload)
