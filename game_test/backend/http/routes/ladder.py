"""Ladder and small-account transport routes."""

from flask import Blueprint, jsonify, request

from ...application.ladder_service import (
    challenge_ladder,
    get_ladder_auto_status,
    get_small_status,
    invite_team,
    list_small_accounts,
    remove_small_account,
    reorder_small_account,
    save_small_account,
    start_ladder_auto,
    start_small_account,
    start_small_accounts,
    stop_ladder_auto,
    stop_small_account,
    stop_small_accounts,
)
from ..serializers import _json_ok
from ..validators import get_json_body

blueprint = Blueprint("http_ladder", __name__, url_prefix="/api")


@blueprint.route("/ladder/challenge", methods=["POST"])
def api_ladder_challenge():
    return _json_ok(challenge_ladder(get_json_body()))


@blueprint.route("/ladder/auto/start", methods=["POST"])
def api_ladder_auto_start():
    return _json_ok(start_ladder_auto(get_json_body()))


@blueprint.route("/ladder/auto/stop", methods=["POST"])
def api_ladder_auto_stop():
    return _json_ok(stop_ladder_auto())


@blueprint.route("/ladder/auto/status", methods=["GET"])
def api_ladder_auto_status():
    return jsonify(get_ladder_auto_status())


@blueprint.route("/ladder/team/invite", methods=["POST"])
def api_ladder_team_invite():
    return _json_ok(invite_team(get_json_body()))


@blueprint.route("/ladder/small-accounts", methods=["GET", "POST"])
def api_ladder_small_accounts():
    if request.method == "POST":
        return _json_ok(save_small_account(get_json_body()))
    return jsonify(list_small_accounts())


@blueprint.route("/ladder/small-accounts/<account>", methods=["PUT", "DELETE"])
def api_ladder_small_account_item(account: str):
    if request.method == "DELETE":
        return jsonify(remove_small_account(account))
    body = get_json_body()
    direction = str(body.get("direction", "")).strip().lower()
    if direction:
        return _json_ok(reorder_small_account(account, direction))
    merged = dict(body)
    merged["account"] = account
    return _json_ok(save_small_account(merged))


@blueprint.route("/ladder/small/start", methods=["POST"])
def api_ladder_small_start():
    return _json_ok(start_small_accounts())


@blueprint.route("/ladder/small/start-one", methods=["POST"])
def api_ladder_small_start_one():
    body = get_json_body()
    return _json_ok(start_small_account(str(body.get("account", "")).strip()))


@blueprint.route("/ladder/small/stop", methods=["POST"])
def api_ladder_small_stop():
    return _json_ok(stop_small_accounts())


@blueprint.route("/ladder/small/stop-one", methods=["POST"])
def api_ladder_small_stop_one():
    body = get_json_body()
    return _json_ok(stop_small_account(str(body.get("account", "")).strip()))


@blueprint.route("/ladder/small/status", methods=["GET"])
def api_ladder_small_status():
    return jsonify(get_small_status())
