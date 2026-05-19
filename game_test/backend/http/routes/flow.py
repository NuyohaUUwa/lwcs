"""Complex flow transport routes (star-stone, transport-supply, liaoguo, world-boss)."""

from flask import Blueprint, jsonify

from ...application.flow_service import (
    get_liaoguo_status,
    get_star_stone_status,
    get_synthesis_batch_status,
    get_transport_supply_status,
    get_world_boss_status,
    send_daily_checkin,
    start_liaoguo,
    start_star_stone_loop,
    start_synthesis_batch,
    start_transport_supply,
    start_world_boss,
    stop_liaoguo,
    stop_star_stone_loop,
    stop_synthesis_batch,
    stop_transport_supply,
    stop_world_boss,
)
from ..serializers import _json_ok
from ..validators import get_json_body


blueprint = Blueprint("http_flow", __name__, url_prefix="/api")


@blueprint.route("/flow/daily-checkin/run", methods=["POST"])
def api_daily_checkin_run():
    return _json_ok(send_daily_checkin())


@blueprint.route("/flow/star-stone/start", methods=["POST"])
def api_star_stone_start():
    return _json_ok(start_star_stone_loop())


@blueprint.route("/flow/star-stone/stop", methods=["POST"])
def api_star_stone_stop():
    return _json_ok(stop_star_stone_loop())


@blueprint.route("/flow/star-stone/status", methods=["GET"])
def api_star_stone_status():
    return jsonify(get_star_stone_status())


@blueprint.route("/flow/transport-supply/start", methods=["POST"])
def api_transport_supply_start():
    body = get_json_body()
    return _json_ok(start_transport_supply(auto_use_gold_ticket=bool(body.get("auto_use_gold_ticket", False))))


@blueprint.route("/flow/transport-supply/stop", methods=["POST"])
def api_transport_supply_stop():
    return _json_ok(stop_transport_supply())


@blueprint.route("/flow/transport-supply/status", methods=["GET"])
def api_transport_supply_status():
    return jsonify(get_transport_supply_status())


@blueprint.route("/flow/world-boss/start", methods=["POST"])
def api_world_boss_start():
    return _json_ok(start_world_boss())


@blueprint.route("/flow/world-boss/stop", methods=["POST"])
def api_world_boss_stop():
    return _json_ok(stop_world_boss())


@blueprint.route("/flow/world-boss/status", methods=["GET"])
def api_world_boss_status():
    return jsonify(get_world_boss_status())


@blueprint.route("/flow/liaoguo/start", methods=["POST"])
def api_liaoguo_start():
    body = get_json_body()
    return _json_ok(start_liaoguo(body))


@blueprint.route("/flow/liaoguo/stop", methods=["POST"])
def api_liaoguo_stop():
    return _json_ok(stop_liaoguo())


@blueprint.route("/flow/liaoguo/status", methods=["GET"])
def api_liaoguo_status():
    return jsonify(get_liaoguo_status())


@blueprint.route("/flow/synthesis-batch/start", methods=["POST"])
def api_synthesis_batch_start():
    body = get_json_body()
    return _json_ok(start_synthesis_batch(str(body.get("item_id", "")).strip().lower()))


@blueprint.route("/flow/synthesis-batch/stop", methods=["POST"])
def api_synthesis_batch_stop():
    return _json_ok(stop_synthesis_batch())


@blueprint.route("/flow/synthesis-batch/status", methods=["GET"])
def api_synthesis_batch_status():
    return jsonify(get_synthesis_batch_status())
