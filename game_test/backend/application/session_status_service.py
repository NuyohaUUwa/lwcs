"""HTTP-facing session status and control façade."""

from typing import Any

from game_test.backend.runtime import get_session
from game_test.backend.runtime.flow import ensure_control_worker_running, set_auto_reconnect_enabled


def bootstrap_control_worker() -> None:
    ensure_control_worker_running()


def get_live_session():
    return get_session()


def get_status_snapshot() -> dict[str, Any]:
    return get_session().get_status()


def get_control_state_snapshot() -> dict[str, Any]:
    session = get_session()
    return {
        "ok": True,
        "control_state": session.get_control_state(),
        "battle_state": session.get_status().get("battle_state", {}),
    }


def update_control_config(*, auto_reconnect: bool) -> dict[str, Any]:
    return set_auto_reconnect_enabled(bool(auto_reconnect))
