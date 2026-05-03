"""Packet, fingerprint, and probe transport routes."""

from flask import Blueprint, jsonify, request

from ...application.packet_service import (
    annotate_packet as app_annotate_packet,
    clear_packets,
    delete_fingerprint,
    get_fingerprints,
    list_packets,
    parse_probe_packet,
    send_probe_packet as app_send_probe_packet,
    update_fingerprint,
)
from ..serializers import _json_ok
from ..validators import get_json_body


blueprint = Blueprint("http_packet", __name__, url_prefix="/api")


@blueprint.route("/packets", methods=["GET", "DELETE"])
def api_packets():
    if request.method == "DELETE":
        return jsonify(clear_packets())

    limit = int(request.args.get("limit", 100))
    direction = request.args.get("direction", "").upper() or None
    parsed_param = request.args.get("parsed", "")
    parsed_only = True if parsed_param.lower() == "true" else False if parsed_param.lower() == "false" else None
    annotated_param = request.args.get("annotated", "")
    annotated_only = True if annotated_param.lower() == "true" else None
    return jsonify(
        list_packets(
            limit=limit,
            direction=direction,
            parsed_only=parsed_only,
            annotated_only=annotated_only,
        )
    )


@blueprint.route("/packets/<int:packet_id>/annotate", methods=["POST"])
def api_annotate_packet(packet_id: int):
    body = get_json_body()
    return _json_ok(app_annotate_packet(packet_id, body.get("text", "").strip()), fallback_status=404)


@blueprint.route("/fingerprints", methods=["GET"])
def api_get_fingerprints():
    return jsonify(get_fingerprints())


@blueprint.route("/fingerprints/<fp>", methods=["PUT", "DELETE"])
def api_update_fingerprint(fp: str):
    if request.method == "PUT":
        body = get_json_body()
        return _json_ok(update_fingerprint(fp, body.get("description", "")))

    return jsonify(delete_fingerprint(fp))


@blueprint.route("/probe/send", methods=["POST"])
def api_probe_send():
    body = get_json_body()
    hex_str = body.get("hex", "").strip()
    use_queue = bool(body.get("use_queue", True))
    priority = int(body.get("priority", 10))
    if not hex_str:
        return jsonify({"ok": False, "error": "hex 不能为空"}), 400
    return _json_ok(app_send_probe_packet(hex_str=hex_str, use_queue=use_queue, priority=priority))


@blueprint.route("/probe/parse", methods=["POST"])
def api_probe_parse():
    body = get_json_body()
    hex_str = body.get("hex", "").replace(" ", "").lower()
    if not hex_str:
        return jsonify({"ok": False, "error": "hex 不能为空"}), 400
    return jsonify(parse_probe_packet(hex_str))
