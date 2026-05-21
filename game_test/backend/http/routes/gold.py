"""Gold status and transfer routes."""

from flask import Blueprint, jsonify

from ...application.gold_service import deposit_gold, get_gold, refresh_gold, withdraw_gold
from ..serializers import _json_ok


blueprint = Blueprint("http_gold", __name__, url_prefix="/api")


@blueprint.route("/gold", methods=["GET"])
def api_gold():
    return jsonify(get_gold())


@blueprint.route("/gold/refresh", methods=["POST"])
def api_gold_refresh():
    return _json_ok(refresh_gold())


@blueprint.route("/gold/deposit", methods=["POST"])
def api_gold_deposit():
    return _json_ok(deposit_gold())


@blueprint.route("/gold/withdraw", methods=["POST"])
def api_gold_withdraw():
    return _json_ok(withdraw_gold())
