"""HTTP-facing gold façade."""

import threading
from typing import Any

from game_test.backend.domain.inventory.gold import (
    GOLD_DEPOSIT_PACKET,
    GOLD_REFRESH_PACKET,
    GOLD_WITHDRAW_PACKET,
)
from game_test.backend.runtime import get_session
from game_test.backend.runtime.actions import send_raw_action

GOLD_REFRESH_AFTER_ACTION_DELAY_S = 1.2


def get_gold() -> dict[str, Any]:
    return {"ok": True, "gold": get_session().get_gold_snapshot()}


def refresh_gold() -> dict[str, Any]:
    res = send_raw_action(GOLD_REFRESH_PACKET, priority=10, use_queue=True)
    if not res.get("ok"):
        return res
    return {"ok": True, "queued": res.get("queued", 1), "gold": get_session().get_gold_snapshot()}


def schedule_gold_refresh(*, delay_s: float = GOLD_REFRESH_AFTER_ACTION_DELAY_S) -> None:
    def _run():
        send_raw_action(GOLD_REFRESH_PACKET, priority=10, use_queue=True)

    timer = threading.Timer(max(0.0, float(delay_s)), _run)
    timer.daemon = True
    timer.start()


def deposit_gold() -> dict[str, Any]:
    res = send_raw_action(GOLD_DEPOSIT_PACKET, priority=10, use_queue=True)
    if not res.get("ok"):
        return res
    schedule_gold_refresh()
    return {"ok": True, "queued": int(res.get("queued", 1) or 1), "scheduled_refresh": True}


def withdraw_gold() -> dict[str, Any]:
    res = send_raw_action(GOLD_WITHDRAW_PACKET, priority=10, use_queue=True)
    if not res.get("ok"):
        return res
    schedule_gold_refresh()
    return {"ok": True, "queued": int(res.get("queued", 1) or 1), "scheduled_refresh": True}
