"""
轻量数据服务。
负责 monsters / quick_logins 等 JSON 文件的读写与 session 缓存同步。
"""

import json
import os
import time

from features.battle import DEFAULT_MONSTERS
from core.session import get_session

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
MONSTERS_FILE = os.path.join(DATA_DIR, "monsters.json")
QUICK_LOGINS_FILE = os.path.join(DATA_DIR, "quick_logins.json")
BUY_ITEMS_FILE = os.path.join(DATA_DIR, "buy_items.json")


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


def load_buy_items():
    data = _read_json_file(BUY_ITEMS_FILE, [])
    items = data if isinstance(data, list) else []
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        code = str(item.get("code", "")).strip().lower()
        if not name or len(code) != 22:
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
    if len(code) != 22:
        return {"ok": False, "error": "code 必须是 22 位 hex"}
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
