"""HTTP-facing authentication flow façade."""

import time
from typing import Any

from game_test.backend.runtime.flow import (
    disconnect_flow as app_disconnect,
    fetch_roles_flow,
    login_flow,
    select_role_flow,
)

RECONNECT_STEP_DELAY_S = 1.5
SELECT_ROLE_RETRY_TIMES = 2
QUICK_LOGIN_RETRY_GAP_S = 1.6
QUICK_LOGIN_MAX_ATTEMPTS = 3
ROLE_STATS_WAIT_TIMEOUT_S = 5.0
ROLE_STATS_POLL_INTERVAL_S = 0.25


def _wait_role_stats_ready() -> dict[str, Any]:
    """Poll until role stats are non-empty or timeout."""
    from game_test.backend.application.role_service import get_role_stats_snapshot
    deadline = time.monotonic() + ROLE_STATS_WAIT_TIMEOUT_S
    while time.monotonic() < deadline:
        res = get_role_stats_snapshot()
        if res.get("ok"):
            stats = res.get("stats") or {}
            if stats and len(stats) > 0:
                return {"ok": True, "stats": stats}
        time.sleep(ROLE_STATS_POLL_INTERVAL_S)
    return {"ok": False, "error": "重连后未获取到角色属性"}


def login(account: str, password: str, server: str) -> dict[str, Any]:
    return login_flow(account, password, server)


def execute_full_login_flow(
    account: str,
    password: str,
    server: str,
    login_server: str,
    server_ip: str,
    server_port: int,
    role_id: str,
    *,
    require_role_stats: bool = True,
    max_attempts: int = 1,
) -> dict[str, Any]:
    """
    Orchestrate login → fetch roles → select role in one backend call.
    Handles timing delays, retry logic, disconnect between attempts,
    and optional role stats readiness wait that was previously in frontend JS.
    """
    last_error = "未知错误"
    max_attempts = max(1, min(10, max_attempts))

    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            app_disconnect()
            time.sleep(QUICK_LOGIN_RETRY_GAP_S)

        login_res = login_flow(account, password, login_server)
        if not login_res.get("ok"):
            last_error = login_res.get("error", "登录失败")
            continue

        time.sleep(RECONNECT_STEP_DELAY_S)

        resolved_server_name = "" if (server_ip and server_port) else server
        roles_res = fetch_roles_flow(
            server_ip=server_ip,
            server_port=server_port,
            server_name=resolved_server_name,
        )
        if not roles_res.get("ok"):
            last_error = roles_res.get("error", "选区失败")
            continue

        time.sleep(RECONNECT_STEP_DELAY_S)

        last_enter_res = None
        for inner_attempt in range(1, SELECT_ROLE_RETRY_TIMES + 2):
            enter_res = select_role_flow(role_id)
            last_enter_res = enter_res
            if enter_res.get("ok"):
                if require_role_stats:
                    stats_ready = _wait_role_stats_ready()
                    if not stats_ready.get("ok"):
                        last_error = stats_ready.get("error", "未获取到角色属性")
                        if attempt < max_attempts:
                            app_disconnect()
                            continue
                        return {"ok": False, "error": last_error}
                return {"ok": True, "role": enter_res.get("role")}
            if inner_attempt <= SELECT_ROLE_RETRY_TIMES:
                time.sleep(RECONNECT_STEP_DELAY_S)

        last_error = last_enter_res.get("error") if last_enter_res else "选角失败"
        if attempt < max_attempts:
            app_disconnect()

    return {"ok": False, "error": last_error}


def disconnect() -> dict[str, Any]:
    return app_disconnect()
