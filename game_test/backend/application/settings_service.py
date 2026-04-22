"""HTTP-facing persisted settings façade."""

from typing import Any

from game_test.backend.infrastructure.config import GAME_SERVERS, LOGIN_SERVERS
from game_test.backend.infrastructure.data_manager import (
    delete_buy_item,
    delete_liaoguo_pair,
    delete_quick_login,
    load_buy_items,
    load_liaoguo_pairs,
    load_quick_logins,
    upsert_buy_item,
    upsert_liaoguo_pair,
    upsert_quick_login,
)


def get_servers_catalog() -> dict[str, Any]:
    return {
        "login_servers": list(LOGIN_SERVERS.keys()),
        "game_servers": [{"name": name, "ip": cfg["ip"], "port": cfg["port"]} for name, cfg in GAME_SERVERS.items()],
    }


def get_quick_logins() -> dict[str, Any]:
    return {"ok": True, "items": load_quick_logins()}


def save_quick_login(body: dict[str, Any]) -> dict[str, Any]:
    return upsert_quick_login(body)


def remove_quick_login(item_id: str) -> dict[str, Any]:
    return delete_quick_login(item_id)


def get_buy_items() -> dict[str, Any]:
    return {"ok": True, "items": load_buy_items()}


def save_buy_item(body: dict[str, Any]) -> dict[str, Any]:
    return upsert_buy_item(body)


def remove_buy_item(item_id: str) -> dict[str, Any]:
    return delete_buy_item(item_id)


def get_liaoguo_pairs() -> dict[str, Any]:
    return {"ok": True, "items": load_liaoguo_pairs()}


def save_liaoguo_pair(body: dict[str, Any]) -> dict[str, Any]:
    return upsert_liaoguo_pair(body)


def remove_liaoguo_pair(item_id: str) -> dict[str, Any]:
    return delete_liaoguo_pair(item_id)
