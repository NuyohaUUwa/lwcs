"""
Flask HTTP API 服务 + SSE 实时推送。
启动方式：cd game_test && python api/server.py
"""

import sys
import os

# 将 game_test 目录加入 sys.path，使 core/features/config 可以直接导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import time
import queue

from flask import Flask, request, jsonify, Response, send_from_directory
from flask_cors import CORS

from core.session import get_session
from config import API_HOST, API_PORT, API_DEBUG, LOGIN_SERVERS, GAME_SERVERS

from features.login import do_login
from features.roles import fetch_roles, select_role
from features.backpack import dispatch_backpack_packet, get_backpack_snapshot
from features.item_use import use_item, drop_item, decompose_item, exchange_wuling, one_key_decompose
from features.chat import send_chat
from features.packet_probe import record_packet, annotate_packet, send_probe_packet, try_parse_packet, get_all_fingerprints
from features.role_stats import update_session_stats
from features.battle import (
    DEFAULT_MONSTERS,
    start_battle,
    do_battle,
    one_shot_kill,
    parse_battle_response,
    parse_battle_end,
)

app = Flask(__name__, static_folder=None)
CORS(app)

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
MONSTERS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "monsters.json")
QUICK_LOGINS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "quick_logins.json")


# ================================================================== #
#  下行报文分发（收包线程回调）                                          #
# ================================================================== #

def dispatch_packet(raw_bytes: bytes):
    """
    收包线程回调：
    1. 记录到 packet_probe（自动解析 + 广播 SSE）
    2. 分发给背包解析模块
    3. 更新心跳时间戳
    """
    if not raw_bytes:
        return
    hex_str = raw_bytes.hex()
    get_session().last_recv_ts = time.time()
    # 记录报文
    record_packet(hex_str, "DN")
    # d607：背包 + 角色属性解析
    if "d607" in hex_str[8:20]:
        try:
            dispatch_backpack_packet(hex_str)
        except Exception as e:
            print(f"[server] 背包解析异常: {e}")
        try:
            update_session_stats(hex_str)
        except Exception as e:
            print(f"[server] 角色属性解析异常: {e}")
    elif "de07" in hex_str[8:20]:
        try:
            parse_battle_response(hex_str)
        except Exception as e:
            print(f"[server] 战斗响应解析异常: {e}")
    elif "df07" in hex_str[8:20]:
        try:
            parse_battle_end(hex_str)
        except Exception as e:
            print(f"[server] 战斗结束解析异常: {e}")
    else:
        try:
            dispatch_backpack_packet(hex_str)
        except Exception as e:
            print(f"[server] 背包解析异常: {e}")


# ================================================================== #
#  静态前端                                                            #
# ================================================================== #

@app.route("/", methods=["GET"])
def index():
    return send_from_directory(WEB_DIR, "index.html")


# ================================================================== #
#  状态                                                                #
# ================================================================== #

@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify(get_session().get_status())


@app.route("/api/servers", methods=["GET"])
def api_servers():
    """返回可用登录服和游戏服列表。"""
    return jsonify({
        "login_servers": list(LOGIN_SERVERS.keys()),
        "game_servers": [
            {"name": k, "ip": v["ip"], "port": v["port"]}
            for k, v in GAME_SERVERS.items()
        ],
    })


# ================================================================== #
#  登录                                                                #
# ================================================================== #

@app.route("/api/login", methods=["POST"])
def api_login():
    """
    POST /api/login
    Body: {"account": "...", "password": "...", "server": "龙一服"}
    """
    body = request.get_json(silent=True) or {}
    account = body.get("account", "").strip()
    password = body.get("password", "").strip()
    server = body.get("server", "").strip()

    if not account or not password or not server:
        return jsonify({"ok": False, "error": "account / password / server 不能为空"}), 400

    result = do_login(account, password, server)
    status = 200 if result["ok"] else 400
    return jsonify(result), status


# ================================================================== #
#  角色                                                                #
# ================================================================== #

@app.route("/api/roles", methods=["POST"])
def api_fetch_roles():
    """
    POST /api/roles
    Body: {"server_ip": "...", "server_port": 12065}
    或使用预设服务器名称：{"server_name": "龙一服"}
    """
    body = request.get_json(silent=True) or {}
    server_name = body.get("server_name", "").strip()
    if server_name and server_name in GAME_SERVERS:
        srv = GAME_SERVERS[server_name]
        server_ip = srv["ip"]
        server_port = srv["port"]
    else:
        server_ip = body.get("server_ip", "").strip()
        server_port = int(body.get("server_port", 0))

    if not server_ip or not server_port:
        return jsonify({"ok": False, "error": "需要提供 server_ip + server_port 或 server_name"}), 400

    result = fetch_roles(server_ip, server_port)
    return jsonify(result), 200 if result["ok"] else 400


@app.route("/api/select-role", methods=["POST"])
def api_select_role():
    """
    POST /api/select-role
    Body: {"role_id": "485302"}
    """
    body = request.get_json(silent=True) or {}
    role_id = body.get("role_id", "").strip()
    if not role_id:
        return jsonify({"ok": False, "error": "role_id 不能为空"}), 400

    result = select_role(role_id, dispatch_packet)
    return jsonify(result), 200 if result["ok"] else 400


# ================================================================== #
#  背包                                                                #
# ================================================================== #

@app.route("/api/backpack", methods=["GET"])
def api_backpack():
    return jsonify({"ok": True, "items": get_backpack_snapshot()})


@app.route("/api/backpack/refresh", methods=["POST"])
def api_backpack_refresh():
    """
    手动刷新背包（对齐 main-000.py _refresh_backpack_manual）：
    1. 从当前 session 取最新背包快照
    2. 广播 SSE backpack 事件（确保所有前端客户端同步）
    3. 返回物品列表和数量
    """
    session = get_session()
    print("[backpack] 手动刷新背包...")
    items = get_backpack_snapshot()
    count = len(items)
    # 广播 SSE，确保所有前端同步到最新数据
    session.notify_backpack_update()
    print(f"[backpack] 背包当前物品数量: {count}")
    return jsonify({"ok": True, "items": items, "count": count})


@app.route("/api/role-stats", methods=["GET"])
def api_role_stats():
    from features.role_stats import STAT_GROUPS, STAT_NAMES
    session = get_session()
    with session._lock:
        stats = dict(session.role_stats)
    return jsonify({
        "ok": True,
        "stats": stats,
        "groups": STAT_GROUPS,
        "order": STAT_NAMES,
    })


# ================================================================== #
#  物品操作                                                            #
# ================================================================== #

@app.route("/api/item/use", methods=["POST"])
def api_item_use():
    body = request.get_json(silent=True) or {}
    item_id = body.get("item_id", "").strip()
    quantity = int(body.get("quantity", 1))
    if not item_id:
        return jsonify({"ok": False, "error": "item_id 不能为空"}), 400
    # 记录上行报文（由 item_use 内部构建，此处记录原始意图）
    return jsonify(use_item(item_id, quantity))


@app.route("/api/item/drop", methods=["POST"])
def api_item_drop():
    body = request.get_json(silent=True) or {}
    item_id = body.get("item_id", "").strip()
    quantity = int(body.get("quantity", 1))
    if not item_id:
        return jsonify({"ok": False, "error": "item_id 不能为空"}), 400
    return jsonify(drop_item(item_id, quantity))


@app.route("/api/item/decompose", methods=["POST"])
def api_item_decompose():
    body = request.get_json(silent=True) or {}
    item_id = body.get("item_id", "").strip()
    if not item_id:
        return jsonify({"ok": False, "error": "item_id 不能为空"}), 400
    return jsonify(decompose_item(item_id))


@app.route("/api/item/decompose-all", methods=["POST"])
def api_item_decompose_all():
    body = request.get_json(silent=True) or {}
    protected = body.get("protected_items", [])
    return jsonify(one_key_decompose(protected))


@app.route("/api/item/exchange-wuling", methods=["POST"])
def api_exchange_wuling():
    return jsonify(exchange_wuling())


# ================================================================== #
#  聊天                                                                #
# ================================================================== #

@app.route("/api/chat", methods=["POST"])
def api_chat():
    body = request.get_json(silent=True) or {}
    message = body.get("message", "").strip()
    if not message:
        return jsonify({"ok": False, "error": "message 不能为空"}), 400
    return jsonify(send_chat(message))


# ================================================================== #
#  战斗                                                                #
# ================================================================== #

def _get_monsters():
    session = get_session()
    with session._lock:
        monsters = getattr(session, "monsters", None)
        if monsters is None:
            monsters = None
            try:
                with open(MONSTERS_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, list):
                    tmp = []
                    for item in raw:
                        if isinstance(item, dict):
                            name = str(item.get("name", "")).strip()
                            code = str(item.get("code", "")).strip().lower()
                            if name and len(code) == 4:
                                try:
                                    int(code, 16)
                                    tmp.append({"name": name, "code": code})
                                except ValueError:
                                    pass
                    if tmp:
                        monsters = tmp
            except Exception:
                monsters = None
            if monsters is None:
                monsters = [dict(x) for x in DEFAULT_MONSTERS]
            session.monsters = monsters
        return [dict(x) for x in monsters]


def _save_monsters(monsters):
    session = get_session()
    with session._lock:
        session.monsters = [dict(x) for x in monsters]
    # 持久化到 data/monsters.json，后续只改 JSON 即可
    try:
        with open(MONSTERS_FILE, "w", encoding="utf-8") as f:
            json.dump([dict(x) for x in monsters], f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[server] 写入 monsters.json 失败: {e}")
    session._notify_sse("monsters", [dict(x) for x in monsters])


def _load_quick_logins():
    try:
        with open(QUICK_LOGINS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _save_quick_logins(items):
    try:
        with open(QUICK_LOGINS_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[server] 写入 quick_logins.json 失败: {e}")


@app.route("/api/battle/start", methods=["POST"])
def api_battle_start():
    body = request.get_json(silent=True) or {}
    monster_code = (body.get("monster_code") or "").strip().lower()
    if not monster_code:
        return jsonify({"ok": False, "error": "monster_code 不能为空"}), 400
    result = start_battle(monster_code)
    return jsonify(result), 200 if result.get("ok") else 400


@app.route("/api/battle/do", methods=["POST"])
def api_battle_do():
    result = do_battle()
    return jsonify(result), 200 if result.get("ok") else 400


@app.route("/api/battle/one-shot", methods=["POST"])
def api_battle_one_shot():
    body = request.get_json(silent=True) or {}
    monster_code = (body.get("monster_code") or "").strip().lower()
    if not monster_code:
        return jsonify({"ok": False, "error": "monster_code 不能为空"}), 400
    result = one_shot_kill(monster_code)
    return jsonify(result), 200 if result.get("ok") else 400


@app.route("/api/battle/monsters", methods=["GET"])
def api_battle_monsters_get():
    return jsonify({"ok": True, "monsters": _get_monsters()})


@app.route("/api/battle/monsters", methods=["POST"])
def api_battle_monsters_add():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    code = (body.get("code") or "").strip().lower()
    if not name:
        return jsonify({"ok": False, "error": "name 不能为空"}), 400
    if len(code) != 4:
        return jsonify({"ok": False, "error": "code 必须是 4 位 hex"}), 400
    try:
        int(code, 16)
    except ValueError:
        return jsonify({"ok": False, "error": "code 不是合法 hex"}), 400

    monsters = _get_monsters()
    exists = next((m for m in monsters if m.get("code") == code), None)
    if exists:
        exists["name"] = name
    else:
        monsters.append({"name": name, "code": code})
    _save_monsters(monsters)
    return jsonify({"ok": True, "monsters": monsters})


@app.route("/api/battle/monsters/<code>", methods=["DELETE"])
def api_battle_monsters_delete(code: str):
    code = (code or "").strip().lower()
    monsters = _get_monsters()
    new_list = [m for m in monsters if (m.get("code") or "").lower() != code]
    if len(new_list) == len(monsters):
        return jsonify({"ok": False, "error": "怪物代码不存在"}), 404
    _save_monsters(new_list)
    return jsonify({"ok": True, "monsters": new_list})


@app.route("/api/quick-logins", methods=["GET"])
def api_quick_logins_get():
    return jsonify({"ok": True, "items": _load_quick_logins()})


@app.route("/api/quick-logins", methods=["POST"])
def api_quick_logins_save():
    body = request.get_json(silent=True) or {}
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
        return jsonify({"ok": False, "error": "缺少必要字段"}), 400

    items = _load_quick_logins()
    key = f"{account}|{server_ip}:{server_port}|{role_id}"
    now_ts = int(time.time())
    now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts))
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
        "saved_at": now_str,
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
    _save_quick_logins(items)
    return jsonify({"ok": True, "items": items, "saved_id": key})


@app.route("/api/quick-logins/<item_id>", methods=["DELETE"])
def api_quick_logins_delete(item_id: str):
    items = _load_quick_logins()
    new_items = [x for x in items if x.get("id") != item_id]
    _save_quick_logins(new_items)
    return jsonify({"ok": True, "items": new_items})


# ================================================================== #
#  报文探测                                                            #
# ================================================================== #

@app.route("/api/packets", methods=["GET"])
def api_packets():
    """
    GET /api/packets?limit=100&direction=DN&parsed=false&annotated=false
    """
    limit = int(request.args.get("limit", 100))
    direction = request.args.get("direction", "").upper() or None

    parsed_param = request.args.get("parsed", "")
    parsed_only = None
    if parsed_param.lower() == "true":
        parsed_only = True
    elif parsed_param.lower() == "false":
        parsed_only = False

    annotated_param = request.args.get("annotated", "")
    annotated_only = None
    if annotated_param.lower() == "true":
        annotated_only = True

    records = get_session().get_packet_log(
        limit=limit,
        direction=direction,
        parsed_only=parsed_only,
        annotated_only=annotated_only,
    )
    return jsonify({"ok": True, "total": len(records), "packets": records})


@app.route("/api/packets/<int:packet_id>/annotate", methods=["POST"])
def api_annotate_packet(packet_id: int):
    """
    POST /api/packets/{id}/annotate
    Body: {"text": "点击挑战时发出"}
    """
    body = request.get_json(silent=True) or {}
    text = body.get("text", "").strip()
    result = annotate_packet(packet_id, text)
    return jsonify(result), 200 if result["ok"] else 404


@app.route("/api/fingerprints", methods=["GET"])
def api_get_fingerprints():
    """
    GET /api/fingerprints
    返回当前全量指纹描述表 {fingerprint: description}
    """
    return jsonify({"ok": True, "fingerprints": get_all_fingerprints()})


@app.route("/api/fingerprints/<fp>", methods=["PUT", "DELETE"])
def api_update_fingerprint(fp: str):
    """
    PUT  /api/fingerprints/{fp}  Body: {"description": "..."}  — 新增/更新指纹描述
    DELETE /api/fingerprints/{fp}                              — 删除指纹描述
    """
    from features.packet_probe import _fingerprints, _fingerprints_lock, _save_fingerprints
    fp = fp.lower()
    if request.method == "PUT":
        body = request.get_json(silent=True) or {}
        desc = body.get("description", "").strip()
        if not desc:
            return jsonify({"ok": False, "error": "description 不能为空"}), 400
        with _fingerprints_lock:
            _fingerprints[fp] = desc
        _save_fingerprints()
        return jsonify({"ok": True, "fingerprint": fp, "description": desc})
    else:  # DELETE
        with _fingerprints_lock:
            removed = _fingerprints.pop(fp, None)
        _save_fingerprints()
        return jsonify({"ok": True, "removed": removed is not None})


@app.route("/api/probe/send", methods=["POST"])
def api_probe_send():
    """
    POST /api/probe/send
    Body: {"hex": "1e000000...", "use_queue": true, "priority": 10}
    """
    body = request.get_json(silent=True) or {}
    hex_str = body.get("hex", "").strip()
    use_queue = bool(body.get("use_queue", True))
    priority = int(body.get("priority", 10))

    if not hex_str:
        return jsonify({"ok": False, "error": "hex 不能为空"}), 400

    result = send_probe_packet(hex_str, use_queue=use_queue, priority=priority)
    return jsonify(result), 200 if result["ok"] else 400


@app.route("/api/probe/parse", methods=["POST"])
def api_probe_parse():
    """
    POST /api/probe/parse
    Body: {"hex": "..."}
    纯解析，不发送，用于离线分析报文。
    """
    body = request.get_json(silent=True) or {}
    hex_str = body.get("hex", "").replace(" ", "").lower()
    if not hex_str:
        return jsonify({"ok": False, "error": "hex 不能为空"}), 400
    parsed = try_parse_packet(hex_str)
    return jsonify({"ok": True, "parsed": parsed})


# ================================================================== #
#  SSE 实时推送                                                        #
# ================================================================== #

@app.route("/api/events", methods=["GET"])
def api_events():
    """
    GET /api/events  →  SSE 长连接
    事件格式：data: {"type": "packet|backpack|status|annotation", "data": {...}}\n\n
    """
    session = get_session()
    q = session.subscribe_sse()

    def generate():
        try:
            # 先推送一次当前状态
            yield f"data: {json.dumps({'type': 'status', 'data': session.get_status()}, ensure_ascii=False)}\n\n"
            while True:
                try:
                    payload = q.get(timeout=20)
                    yield f"data: {payload}\n\n"
                except queue.Empty:
                    # 心跳保活
                    yield ": ping\n\n"
        except GeneratorExit:
            pass
        finally:
            session.unsubscribe_sse(q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ================================================================== #
#  断开连接                                                            #
# ================================================================== #

@app.route("/api/disconnect", methods=["POST"])
def api_disconnect():
    session = get_session()
    session.reset()
    return jsonify({"ok": True, "message": "已断开连接"})


# ================================================================== #
#  启动                                                                #
# ================================================================== #

if __name__ == "__main__":
    print(f"游戏测试应用 API 服务启动中，地址: http://{API_HOST}:{API_PORT}")
    print(f"前端页面: http://127.0.0.1:{API_PORT}/")
    app.run(host=API_HOST, port=API_PORT, debug=API_DEBUG, threaded=True)
