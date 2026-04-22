"""Battle and combat transport routes."""

from flask import Blueprint, jsonify

from ...application.battle_service import (
    delete_monster,
    do_battle,
    get_auto_use_config,
    get_monster_list,
    one_shot_battle,
    start_battle,
    start_battle_loop,
    stop_battle_loop,
    update_auto_use_config,
    upsert_monster,
)
from ..serializers import _json_ok
from ..validators import get_json_body


blueprint = Blueprint("http_battle", __name__, url_prefix="/api")


@blueprint.route("/battle/start", methods=["POST"])
def api_battle_start():
    return _json_ok(start_battle(get_json_body()))


@blueprint.route("/battle/do", methods=["POST"])
def api_battle_do():
    return _json_ok(do_battle())


@blueprint.route("/battle/one-shot", methods=["POST"])
def api_battle_one_shot():
    return _json_ok(one_shot_battle(get_json_body()))


@blueprint.route("/battle/loop/start", methods=["POST"])
def api_battle_loop_start():
    return _json_ok(start_battle_loop(get_json_body()))


@blueprint.route("/battle/loop/stop", methods=["POST"])
def api_battle_loop_stop():
    return _json_ok(stop_battle_loop(get_json_body()))


@blueprint.route("/battle/monsters", methods=["GET"])
def api_battle_monsters_get():
    return jsonify(get_monster_list())


@blueprint.route("/battle/monsters", methods=["POST"])
def api_battle_monsters_add():
    body = get_json_body()
    return _json_ok(upsert_monster(name=body.get("name", ""), code=body.get("code", "")))


@blueprint.route("/battle/monsters/<code>", methods=["DELETE"])
def api_battle_monsters_delete(code: str):
    result = delete_monster(code)
    return jsonify(result), 200 if result.get("ok") else 404


@blueprint.route("/auto-use/config", methods=["GET"])
def api_auto_use_get():
    return jsonify(get_auto_use_config())


@blueprint.route("/auto-use/config", methods=["PUT"])
def api_auto_use_put():
    body = get_json_body()
    return _json_ok(update_auto_use_config(body.get("rules", [])))
