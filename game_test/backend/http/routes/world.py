"""World, movement, and chat transport routes."""

from flask import Blueprint, jsonify

from ...application.world_service import (
    get_teleport_destinations_snapshot,
    parse_map_npc_packet,
    send_chat,
    teleport,
)
from ..serializers import _json_ok
from ..validators import get_json_body
from ...infrastructure.config import DEFAULT_MAP_NPC_ID_HEX, DEFAULT_MAP_NPC_UTF8_TEXT


blueprint = Blueprint("http_world", __name__, url_prefix="/api")


@blueprint.route("/chat", methods=["POST"])
def api_chat():
    return _json_ok(send_chat(get_json_body()))


@blueprint.route("/teleport/destinations", methods=["GET"])
def api_teleport_destinations():
    return jsonify(get_teleport_destinations_snapshot())


@blueprint.route("/teleport", methods=["POST"])
def api_teleport():
    return _json_ok(teleport(get_json_body()))


@blueprint.route("/map-npc/parse", methods=["POST"])
def api_map_npc_parse():
    """解析整包 hex 中的「当前地图 NPC」"""
    body = get_json_body()
    hex_str = str(body.get("raw_hex", body.get("hex", ""))).replace(" ", "").lower()
    if not hex_str:
        return jsonify({"ok": False, "error": "raw_hex 不能为空"}), 400
    return jsonify(parse_map_npc_packet(hex_str))


@blueprint.route("/map-npc/current", methods=["POST"])
def api_map_npc_current():
    """该版本固定使用 config 默认 NPC；保留接口但不再改变会话状态。"""
    _ = get_json_body()  # consume body for compatibility
    return _json_ok(
        {
            "ok": True,
            "current_map_npc": {"id_hex": DEFAULT_MAP_NPC_ID_HEX, "utf8_text": DEFAULT_MAP_NPC_UTF8_TEXT},
        }
    )
