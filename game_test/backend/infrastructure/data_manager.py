"""轻量数据服务唯一实现入口。"""

# pyright: reportAttributeAccessIssue=false, reportMissingTypeArgument=false

import json
import os
import sys
import time

from game_test.backend.domain.combat.battle import DEFAULT_MONSTERS
from game_test.backend.runtime import get_session

from .paths import (
    BUY_ITEMS_FILE,
    LIAOGUO_PAIRS_FILE,
    MONSTERS_FILE,
    QUICK_LOGINS_FILE,
    SCHEDULED_TASKS_FILE,
    SMALL_ACCOUNTS_FILE,
)

_CURRENT_MODULE = sys.modules[__name__]
if __name__ == "backend.infrastructure.data_manager":
    sys.modules.setdefault("game_test.backend.infrastructure.data_manager", _CURRENT_MODULE)
elif __name__ == "game_test.backend.infrastructure.data_manager":
    sys.modules.setdefault("backend.infrastructure.data_manager", _CURRENT_MODULE)


def _read_json_file(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception:
        return default


def _write_json_file(path: str, value):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[data] 写入 {os.path.basename(path)} 失败: {e}")


def get_monsters():
    session = get_session()
    with session._lock:
        monsters = getattr(session, "monsters", None)
        if monsters is None:
            raw = _read_json_file(MONSTERS_FILE, None)
            monsters = []
            if isinstance(raw, list):
                for item in raw:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name", "")).strip()
                    code = str(item.get("code", "")).strip().lower()
                    if not name or len(code) != 4:
                        continue
                    try:
                        int(code, 16)
                    except ValueError:
                        continue
                    monsters.append({"name": name, "code": code})
            if not monsters:
                monsters = [dict(x) for x in DEFAULT_MONSTERS]
            session.monsters = monsters
        return [dict(x) for x in monsters]


def save_monsters(monsters):
    session = get_session()
    with session._lock:
        session.monsters = [dict(x) for x in monsters]
    _write_json_file(MONSTERS_FILE, [dict(x) for x in monsters])
    session._notify_sse("monsters", [dict(x) for x in monsters])


def load_quick_logins():
    data = _read_json_file(QUICK_LOGINS_FILE, [])
    return data if isinstance(data, list) else []


def save_quick_logins(items):
    _write_json_file(QUICK_LOGINS_FILE, items)


def upsert_quick_login(body: dict):
    account = (body.get("account") or "").strip()
    password = (body.get("password") or "").strip()
    login_server = (body.get("login_server") or "").strip()
    server_ip = (body.get("server_ip") or "").strip()
    server_port = int(body.get("server_port", 0))
    server_name = (body.get("server_name") or "").strip()
    role_id = (body.get("role_id") or "").strip()
    role_name = (body.get("role_name") or "").strip()
    role_job = (body.get("role_job") or "").strip()
    if not all([account, password, login_server, server_ip, server_port, role_id]):
        return {"ok": False, "error": "缺少必要字段"}

    items = load_quick_logins()
    key = f"{account}|{server_ip}:{server_port}|{role_id}"
    now_ts = int(time.time())
    entry = {
        "id": key,
        "account": account,
        "password": password,
        "login_server": login_server,
        "server_ip": server_ip,
        "server_port": server_port,
        "server_name": server_name,
        "role_id": role_id,
        "role_name": role_name,
        "role_job": role_job,
        "saved_at_ts": now_ts,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts)),
    }

    replaced = False
    for i, old in enumerate(items):
        if old.get("id") == key:
            items[i] = entry
            replaced = True
            break
    if not replaced:
        items.append(entry)

    items.sort(key=lambda x: int(x.get("saved_at_ts", 0)), reverse=True)
    save_quick_logins(items)
    return {"ok": True, "items": items, "saved_id": key}


def delete_quick_login(item_id: str):
    items = load_quick_logins()
    new_items = [x for x in items if x.get("id") != item_id]
    save_quick_logins(new_items)
    return {"ok": True, "items": new_items}


def _normalize_main_account(main_account: str) -> str:
    return str(main_account or "").strip()


def _normalize_small_account_entry(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None
    account = str(item.get("account", "")).strip()
    password = str(item.get("password", "")).strip()
    if not account:
        return None
    entry = {
        "id": account,
        "account": account,
        "password": password,
        "last_user_id": str(item.get("last_user_id", "")).strip().lower(),
    }
    return entry


def load_small_accounts_data():
    data = _read_json_file(SMALL_ACCOUNTS_FILE, {})
    if isinstance(data, dict) and isinstance(data.get("groups"), dict):
        raw_groups = data.get("groups", {})
    elif isinstance(data, dict):
        raw_groups = data
    else:
        raw_groups = {}
    groups = {}
    for main_account, items in raw_groups.items():
        key = _normalize_main_account(main_account)
        if not key or not isinstance(items, list):
            continue
        normalized = []
        seen = set()
        for item in items:
            entry = _normalize_small_account_entry(item)
            if not entry or entry["account"] in seen:
                continue
            seen.add(entry["account"])
            normalized.append(entry)
        groups[key] = normalized
    return {"groups": groups}


def save_small_accounts_data(data):
    groups = data.get("groups", {}) if isinstance(data, dict) else {}
    _write_json_file(SMALL_ACCOUNTS_FILE, {"groups": groups})


def get_small_accounts(main_account: str):
    key = _normalize_main_account(main_account)
    if not key:
        return {"ok": False, "error": "主号账号为空"}
    data = load_small_accounts_data()
    return {"ok": True, "items": [dict(x) for x in data["groups"].get(key, [])]}


def upsert_small_account(main_account: str, body: dict):
    key = _normalize_main_account(main_account)
    if not key:
        return {"ok": False, "error": "主号账号为空"}
    entry = _normalize_small_account_entry(body)
    if not entry:
        return {"ok": False, "error": "小号账号不能为空"}
    if not entry["password"]:
        return {"ok": False, "error": "小号密码不能为空"}
    data = load_small_accounts_data()
    items = list(data["groups"].get(key, []))
    replaced = False
    for i, old in enumerate(items):
        if old.get("account") == entry["account"]:
            if not entry["last_user_id"]:
                entry["last_user_id"] = str(old.get("last_user_id", "")).strip().lower()
            items[i] = entry
            replaced = True
            break
    if not replaced:
        items.insert(0, entry)
    data["groups"][key] = items
    save_small_accounts_data(data)
    return {"ok": True, "items": [dict(x) for x in items], "saved_id": entry["id"]}


def delete_small_account(main_account: str, account: str):
    key = _normalize_main_account(main_account)
    target = str(account or "").strip()
    if not key:
        return {"ok": False, "error": "主号账号为空"}
    data = load_small_accounts_data()
    items = list(data["groups"].get(key, []))
    new_items = [x for x in items if x.get("account") != target]
    data["groups"][key] = new_items
    save_small_accounts_data(data)
    return {"ok": True, "items": [dict(x) for x in new_items]}


def move_small_account(main_account: str, account: str, direction: str):
    key = _normalize_main_account(main_account)
    target = str(account or "").strip()
    if not key:
        return {"ok": False, "error": "主号账号为空"}
    data = load_small_accounts_data()
    items = list(data["groups"].get(key, []))
    idx = next((i for i, x in enumerate(items) if x.get("account") == target), -1)
    if idx < 0:
        return {"ok": False, "error": "小号不存在"}
    step = -1 if direction == "up" else 1 if direction == "down" else 0
    new_idx = idx + step
    if step and 0 <= new_idx < len(items):
        items[idx], items[new_idx] = items[new_idx], items[idx]
    data["groups"][key] = items
    save_small_accounts_data(data)
    return {"ok": True, "items": [dict(x) for x in items]}


def update_small_account_last_user_id(main_account: str, account: str, user_id: str):
    key = _normalize_main_account(main_account)
    target = str(account or "").strip()
    clean_user_id = str(user_id or "").strip().lower()
    if not key or not target or not clean_user_id:
        return {"ok": False, "error": "缺少必要字段"}
    data = load_small_accounts_data()
    items = list(data["groups"].get(key, []))
    for item in items:
        if item.get("account") == target:
            item["last_user_id"] = clean_user_id
            data["groups"][key] = items
            save_small_accounts_data(data)
            return {"ok": True, "items": [dict(x) for x in items]}
    return {"ok": False, "error": "小号不存在"}


def load_buy_items():
    data = _read_json_file(BUY_ITEMS_FILE, [])
    items = data if isinstance(data, list) else []
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        code = str(item.get("code", "")).strip().lower()
        if not name or len(code) != 14:
            continue
        try:
            int(code, 16)
        except ValueError:
            continue
        out.append({"id": code, "name": name, "code": code})
    return out


def save_buy_items(items):
    _write_json_file(BUY_ITEMS_FILE, items)


def upsert_buy_item(body: dict):
    name = (body.get("name") or "").strip()
    code = (body.get("code") or "").strip().lower()
    if not name:
        return {"ok": False, "error": "name 不能为空"}
    if len(code) != 14:
        return {"ok": False, "error": "code 必须是 14 位 hex"}
    try:
        int(code, 16)
    except ValueError:
        return {"ok": False, "error": "code 不是合法 hex"}

    items = load_buy_items()
    entry = {"id": code, "name": name, "code": code}
    replaced = False
    for i, old in enumerate(items):
        if old.get("id") == code:
            items[i] = entry
            replaced = True
            break
    if not replaced:
        items.append(entry)
    items.sort(key=lambda x: x.get("name", ""))
    save_buy_items(items)
    return {"ok": True, "items": items, "saved_id": code}


def delete_buy_item(item_id: str):
    item_id = (item_id or "").strip().lower()
    items = load_buy_items()
    new_items = [x for x in items if x.get("id") != item_id]
    save_buy_items(new_items)
    return {"ok": True, "items": new_items}


def load_liaoguo_pairs():
    data = _read_json_file(LIAOGUO_PAIRS_FILE, [])
    items = data if isinstance(data, list) else []
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_code = str(item.get("itemCode", "")).strip().lower()
        monster_code = str(item.get("monsterCode", "")).strip().lower()
        label = str(item.get("label", "")).strip()
        task_name = str(item.get("taskName", "")).strip()
        ticket_item_code = str(item.get("ticketItemCode", "")).strip().lower()
        abandon_task_code = str(item.get("abandonTaskCode", "21a1")).strip().lower()
        if len(item_code) != 14 or len(monster_code) != 4:
            continue
        if not label or not task_name:
            continue
        if len(abandon_task_code) != 4:
            continue
        if (
            not ticket_item_code
            or len(ticket_item_code) < 4
            or len(ticket_item_code) > 20
            or len(ticket_item_code) % 2 != 0
        ):
            continue
        try:
            int(item_code, 16)
            int(monster_code, 16)
            int(ticket_item_code, 16)
            int(abandon_task_code, 16)
        except ValueError:
            continue
        out.append(
            {
                "id": task_name,
                "itemCode": item_code,
                "monsterCode": monster_code,
                "label": label,
                "taskName": task_name,
                "ticketItemCode": ticket_item_code,
                "abandonTaskCode": abandon_task_code,
            }
        )
    return out


def save_liaoguo_pairs(items):
    _write_json_file(LIAOGUO_PAIRS_FILE, items)


def upsert_liaoguo_pair(body: dict):
    item_code = str(body.get("itemCode", "")).strip().lower()
    monster_code = str(body.get("monsterCode", "")).strip().lower()
    label = str(body.get("label", "")).strip()
    task_name = str(body.get("taskName", "")).strip()
    ticket_item_code = str(body.get("ticketItemCode", "")).strip().lower()
    abandon_task_code = str(body.get("abandonTaskCode", "21a1")).strip().lower()
    if len(item_code) != 14:
        return {"ok": False, "error": "itemCode 必须是 14 位 hex"}
    if len(monster_code) != 4:
        return {"ok": False, "error": "monsterCode 必须是 4 位 hex"}
    if not label:
        return {"ok": False, "error": "label 不能为空"}
    if not task_name:
        return {"ok": False, "error": "taskName 不能为空"}
    if not ticket_item_code:
        return {"ok": False, "error": "ticketItemCode 不能为空"}
    if len(ticket_item_code) < 4 or len(ticket_item_code) > 20 or len(ticket_item_code) % 2 != 0:
        return {"ok": False, "error": "ticketItemCode 必须是 4~20 位且偶数长度的 hex"}
    if len(abandon_task_code) != 4:
        return {"ok": False, "error": "abandonTaskCode 必须是 4 位 hex"}
    try:
        int(item_code, 16)
        int(monster_code, 16)
        int(ticket_item_code, 16)
        int(abandon_task_code, 16)
    except ValueError:
        return {"ok": False, "error": "itemCode/monsterCode/ticketItemCode/abandonTaskCode 不是合法 hex"}
    entry = {
        "id": task_name,
        "itemCode": item_code,
        "monsterCode": monster_code,
        "label": label,
        "taskName": task_name,
        "ticketItemCode": ticket_item_code,
        "abandonTaskCode": abandon_task_code,
    }
    items = load_liaoguo_pairs()
    replaced = False
    for i, old in enumerate(items):
        if old.get("id") == entry["id"]:
            items[i] = entry
            replaced = True
            break
    if not replaced:
        items.append(entry)
    items.sort(key=lambda x: x.get("label", ""))
    save_liaoguo_pairs(items)
    return {"ok": True, "items": items, "saved_id": entry["id"]}


def delete_liaoguo_pair(item_id: str):
    item_id = (item_id or "").strip().lower()
    items = load_liaoguo_pairs()
    new_items = [x for x in items if (x.get("id") or "").lower() != item_id]
    save_liaoguo_pairs(new_items)
    return {"ok": True, "items": new_items}


DEFAULT_SCHEDULED_TASKS_CONFIG = {
    "daily_checkin": {"enabled": False, "times": [], "run_on_login": False},
    "transport_supply": {"enabled": False, "times": [], "auto_use_gold_ticket": False},
    "world_boss": {"enabled": False, "times": []},
    "liaoguo": {"enabled": False, "time": "", "pair_ids": []},
}


def load_scheduled_tasks_config():
    data = _read_json_file(SCHEDULED_TASKS_FILE, {})
    return data if isinstance(data, dict) else {}


def save_scheduled_tasks_config(config: dict):
    _write_json_file(SCHEDULED_TASKS_FILE, config)


__all__ = [
    "get_monsters",
    "save_monsters",
    "load_quick_logins",
    "save_quick_logins",
    "upsert_quick_login",
    "delete_quick_login",
    "load_small_accounts_data",
    "save_small_accounts_data",
    "get_small_accounts",
    "upsert_small_account",
    "delete_small_account",
    "move_small_account",
    "update_small_account_last_user_id",
    "load_buy_items",
    "save_buy_items",
    "upsert_buy_item",
    "delete_buy_item",
    "load_liaoguo_pairs",
    "save_liaoguo_pairs",
    "upsert_liaoguo_pair",
    "delete_liaoguo_pair",
    "DEFAULT_SCHEDULED_TASKS_CONFIG",
    "load_scheduled_tasks_config",
    "save_scheduled_tasks_config",
]
