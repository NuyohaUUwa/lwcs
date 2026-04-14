"""
Flask HTTP API 服务 + SSE 实时推送。
server.py 只负责参数校验、调用服务层、返回 HTTP / SSE / 静态资源。
"""

import json
import os
import queue
import sys

from flask import Flask, Response, jsonify, request, send_from_directory
try:
    from flask_cors import CORS
except ModuleNotFoundError:  # 允许在未安装 flask-cors 的环境下降级启动
    CORS = None

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import API_DEBUG, API_HOST, API_PORT, GAME_SERVERS, LOGIN_SERVERS
from core.session import get_session
from features.backpack import get_backpack_snapshot
from features.battle import get_auto_use_rules, set_auto_use_rules
from features.map_npc_parse import extract_map_npc_hit
from features.packet_probe import annotate_packet, get_all_fingerprints, send_probe_packet, try_parse_packet
from features.role_stats import STAT_GROUPS, STAT_NAMES
from features.teleport import get_teleport_destinations
from services.action_manager import send_action
from services.data_manager import (
    delete_buy_item,
    delete_quick_login,
    get_monsters,
    load_buy_items,
    load_quick_logins,
    save_monsters,
    upsert_buy_item,
    upsert_quick_login,
)
from services.flow_manager import (
    disconnect_flow,
    ensure_control_worker_running,
    fetch_roles_flow,
    login_flow,
    select_role_flow,
    set_auto_reconnect_enabled,
)

app = Flask(__name__, static_folder=None)
if CORS is not None:
    CORS(app)

ensure_control_worker_running()

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")
def _json_ok(result: dict, fallback_status: int = 400):
    return jsonify(result), 200 if result.get("ok") else fallback_status


@app.route("/", methods=["GET"])
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/<path:path>", methods=["GET"])
def static_files(path: str):
    return send_from_directory(WEB_DIR, path)


@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify(get_session().get_status())


@app.route("/api/control-state", methods=["GET"])
def api_control_state():
    session = get_session()
    return jsonify({"ok": True, "control_state": session.get_control_state(), "battle_state": session.get_status().get("battle_state", {})})


@app.route("/api/control-config", methods=["PUT"])
def api_control_config():
    body = request.get_json(silent=True) or {}
    return _json_ok(set_auto_reconnect_enabled(bool(body.get("auto_reconnect", False))))


@app.route("/api/servers", methods=["GET"])
def api_servers():
    return jsonify(
        {
            "login_servers": list(LOGIN_SERVERS.keys()),
            "game_servers": [{"name": k, "ip": v["ip"], "port": v["port"]} for k, v in GAME_SERVERS.items()],
        }
    )


@app.route("/api/login", methods=["POST"])
def api_login():
    body = request.get_json(silent=True) or {}
    account = body.get("account", "").strip()
    password = body.get("password", "").strip()
    server = body.get("server", "").strip()
    if not account or not password or not server:
        return jsonify({"ok": False, "error": "account / password / server 不能为空"}), 400
    return _json_ok(login_flow(account, password, server))


@app.route("/api/roles", methods=["POST"])
def api_roles():
    body = request.get_json(silent=True) or {}
    server_name = body.get("server_name", "").strip()
    server_ip = body.get("server_ip", "").strip()
    server_port = int(body.get("server_port", 0))
    return _json_ok(fetch_roles_flow(server_ip=server_ip, server_port=server_port, server_name=server_name))


@app.route("/api/select-role", methods=["POST"])
def api_select_role():
    body = request.get_json(silent=True) or {}
    role_id = body.get("role_id", "").strip()
    if not role_id:
        return jsonify({"ok": False, "error": "role_id 不能为空"}), 400
    return _json_ok(select_role_flow(role_id))


@app.route("/api/backpack", methods=["GET"])
def api_backpack():
    return jsonify({"ok": True, "items": get_backpack_snapshot()})


@app.route("/api/backpack/refresh", methods=["POST"])
def api_backpack_refresh():
    session = get_session()
    items = get_backpack_snapshot()
    session.notify_backpack_update()
    return jsonify({"ok": True, "items": items, "count": len(items)})


@app.route("/api/role-stats", methods=["GET"])
def api_role_stats():
    session = get_session()
    with session._lock:
        stats = dict(session.role_stats)
    return jsonify({"ok": True, "stats": stats, "groups": STAT_GROUPS, "order": STAT_NAMES})


@app.route("/api/item/use", methods=["POST"])
def api_item_use():
    body = request.get_json(silent=True) or {}
    return _json_ok(send_action("item.use", body))


@app.route("/api/item/drop", methods=["POST"])
def api_item_drop():
    body = request.get_json(silent=True) or {}
    return _json_ok(send_action("item.drop", body))


@app.route("/api/item/decompose", methods=["POST"])
def api_item_decompose():
    body = request.get_json(silent=True) or {}
    return _json_ok(send_action("item.decompose", body))


@app.route("/api/item/decompose-all", methods=["POST"])
def api_item_decompose_all():
    body = request.get_json(silent=True) or {}
    return _json_ok(send_action("item.decompose_all", body))


@app.route("/api/item/exchange-wuling", methods=["POST"])
def api_exchange_wuling():
    return _json_ok(send_action("item.exchange_wuling", {}))


@app.route("/api/item/buy", methods=["POST"])
def api_item_buy():
    body = request.get_json(silent=True) or {}
    return _json_ok(send_action("item.buy", body))


@app.route("/api/chat", methods=["POST"])
def api_chat():
    body = request.get_json(silent=True) or {}
    return _json_ok(send_action("chat.send", body))


@app.route("/api/teleport/destinations", methods=["GET"])
def api_teleport_destinations():
    return jsonify({"ok": True, "items": get_teleport_destinations()})


@app.route("/api/teleport", methods=["POST"])
def api_teleport():
    body = request.get_json(silent=True) or {}
    return _json_ok(send_action("teleport.go", body))


@app.route("/api/battle/start", methods=["POST"])
def api_battle_start():
    body = request.get_json(silent=True) or {}
    return _json_ok(send_action("battle.start", body))


@app.route("/api/battle/do", methods=["POST"])
def api_battle_do():
    return _json_ok(send_action("battle.do", {}))


@app.route("/api/battle/one-shot", methods=["POST"])
def api_battle_one_shot():
    body = request.get_json(silent=True) or {}
    return _json_ok(send_action("battle.one_shot", body))


@app.route("/api/battle/loop/start", methods=["POST"])
def api_battle_loop_start():
    body = request.get_json(silent=True) or {}
    return _json_ok(send_action("battle.loop.start", body))


@app.route("/api/battle/loop/stop", methods=["POST"])
def api_battle_loop_stop():
    body = request.get_json(silent=True) or {}
    return _json_ok(send_action("battle.loop.stop", body))


@app.route("/api/battle/monsters", methods=["GET"])
def api_battle_monsters_get():
    return jsonify({"ok": True, "monsters": get_monsters()})


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
    monsters = get_monsters()
    exists = next((m for m in monsters if m.get("code") == code), None)
    if exists:
        exists["name"] = name
    else:
        monsters.append({"name": name, "code": code})
    save_monsters(monsters)
    return jsonify({"ok": True, "monsters": monsters})


@app.route("/api/battle/monsters/<code>", methods=["DELETE"])
def api_battle_monsters_delete(code: str):
    code = (code or "").strip().lower()
    monsters = get_monsters()
    new_list = [m for m in monsters if (m.get("code") or "").lower() != code]
    if len(new_list) == len(monsters):
        return jsonify({"ok": False, "error": "怪物代码不存在"}), 404
    save_monsters(new_list)
    return jsonify({"ok": True, "monsters": new_list})


@app.route("/api/auto-use/config", methods=["GET"])
def api_auto_use_get():
    return jsonify({"ok": True, "rules": get_auto_use_rules()})


@app.route("/api/auto-use/config", methods=["PUT"])
def api_auto_use_put():
    body = request.get_json(silent=True) or {}
    rules = body.get("rules", [])
    if not isinstance(rules, list):
        return jsonify({"ok": False, "error": "rules 必须是数组"}), 400
    return jsonify({"ok": True, "rules": set_auto_use_rules(rules)})


@app.route("/api/quick-logins", methods=["GET"])
def api_quick_logins_get():
    return jsonify({"ok": True, "items": load_quick_logins()})


@app.route("/api/quick-logins", methods=["POST"])
def api_quick_logins_save():
    body = request.get_json(silent=True) or {}
    return _json_ok(upsert_quick_login(body))


@app.route("/api/quick-logins/<item_id>", methods=["DELETE"])
def api_quick_logins_delete(item_id: str):
    return jsonify(delete_quick_login(item_id))


@app.route("/api/buy-items", methods=["GET"])
def api_buy_items_get():
    return jsonify({"ok": True, "items": load_buy_items()})


@app.route("/api/buy-items", methods=["POST"])
def api_buy_items_save():
    body = request.get_json(silent=True) or {}
    return _json_ok(upsert_buy_item(body))


@app.route("/api/buy-items/<item_id>", methods=["DELETE"])
def api_buy_items_delete(item_id: str):
    return jsonify(delete_buy_item(item_id))


@app.route("/api/packets", methods=["GET"])
def api_packets():
    limit = int(request.args.get("limit", 100))
    direction = request.args.get("direction", "").upper() or None
    parsed_param = request.args.get("parsed", "")
    parsed_only = True if parsed_param.lower() == "true" else False if parsed_param.lower() == "false" else None
    annotated_param = request.args.get("annotated", "")
    annotated_only = True if annotated_param.lower() == "true" else None
    records = get_session().get_packet_log(
        limit=limit,
        direction=direction,
        parsed_only=parsed_only,
        annotated_only=annotated_only,
    )
    return jsonify({"ok": True, "total": len(records), "packets": records})


@app.route("/api/packets/<int:packet_id>/annotate", methods=["POST"])
def api_annotate_packet(packet_id: int):
    body = request.get_json(silent=True) or {}
    return _json_ok(annotate_packet(packet_id, body.get("text", "").strip()), fallback_status=404)


@app.route("/api/fingerprints", methods=["GET"])
def api_get_fingerprints():
    return jsonify({"ok": True, "fingerprints": get_all_fingerprints()})


@app.route("/api/fingerprints/<fp>", methods=["PUT", "DELETE"])
def api_update_fingerprint(fp: str):
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

    with _fingerprints_lock:
        removed = _fingerprints.pop(fp, None)
    _save_fingerprints()
    return jsonify({"ok": True, "removed": removed is not None})


@app.route("/api/probe/send", methods=["POST"])
def api_probe_send():
    body = request.get_json(silent=True) or {}
    hex_str = body.get("hex", "").strip()
    use_queue = bool(body.get("use_queue", True))
    priority = int(body.get("priority", 10))
    if not hex_str:
        return jsonify({"ok": False, "error": "hex 不能为空"}), 400
    return _json_ok(send_probe_packet(hex_str, use_queue=use_queue, priority=priority))


@app.route("/api/probe/parse", methods=["POST"])
def api_probe_parse():
    body = request.get_json(silent=True) or {}
    hex_str = body.get("hex", "").replace(" ", "").lower()
    if not hex_str:
        return jsonify({"ok": False, "error": "hex 不能为空"}), 400
    return jsonify({"ok": True, "parsed": try_parse_packet(hex_str)})


@app.route("/api/map-npc/parse", methods=["POST"])
def api_map_npc_parse():
    """解析整包 hex 中的「当前地图 NPC」"""
    body = request.get_json(silent=True) or {}
    hex_str = str(body.get("raw_hex", body.get("hex", ""))).replace(" ", "").lower()
    if not hex_str:
        return jsonify({"ok": False, "error": "raw_hex 不能为空"}), 400
    hit = extract_map_npc_hit(hex_str)
    return jsonify({"ok": True, "map_npc": hit})


@app.route("/api/events", methods=["GET"])
def api_events():
    session = get_session()
    q = session.subscribe_sse()

    def generate():
        try:
            yield f"data: {json.dumps({'type': 'status', 'data': session.get_status()}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'control_state', 'data': session.get_control_state()}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'battle_state', 'data': session.get_status().get('battle_state', {})}, ensure_ascii=False)}\n\n"
            while True:
                try:
                    payload = q.get(timeout=20)
                    yield f"data: {payload}\n\n"
                except queue.Empty:
                    yield ": ping\n\n"
        except GeneratorExit:
            pass
        finally:
            session.unsubscribe_sse(q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/disconnect", methods=["POST"])
def api_disconnect():
    return jsonify(disconnect_flow())


if __name__ == "__main__":
    print(f"游戏测试应用 API 服务启动中，地址: http://{API_HOST}:{API_PORT}")
    print(f"前端页面: http://127.0.0.1:{API_PORT}/")
    app.run(host=API_HOST, port=API_PORT, debug=API_DEBUG, threaded=True)
