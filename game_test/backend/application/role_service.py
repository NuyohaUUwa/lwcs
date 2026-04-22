"""HTTP-facing role orchestration façade."""

from typing import Any

from game_test.backend.domain.roles.role_stats import STAT_GROUPS, STAT_NAMES
from game_test.backend.runtime import get_session
from game_test.backend.runtime.flow import fetch_roles_flow, select_role_flow


def fetch_roles(*, server_ip: str, server_port: int, server_name: str) -> dict[str, Any]:
    return fetch_roles_flow(server_ip=server_ip, server_port=server_port, server_name=server_name)


def select_role(role_id: str) -> dict[str, Any]:
    return select_role_flow(role_id)


def get_role_stats_snapshot() -> dict[str, Any]:
    session = get_session()
    with session._lock:
        stats = dict(session.role_stats)
    return {"ok": True, "stats": stats, "groups": STAT_GROUPS, "order": STAT_NAMES}
