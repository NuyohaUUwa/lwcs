"""Inventory and item transport routes."""

from flask import Blueprint, jsonify

from ...application.inventory_service import (
    buy_item,
    decompose_item,
    drop_item,
    exchange_wuling,
    get_auto_decompose_s1_config,
    get_backpack,
    refresh_backpack,
    synthesize_item,
    update_auto_decompose_s1_config,
    use_item,
)
from ..serializers import _json_ok
from ..validators import get_json_body


blueprint = Blueprint("http_inventory", __name__, url_prefix="/api")


@blueprint.route("/backpack", methods=["GET"])
def api_backpack():
    return jsonify(get_backpack())


@blueprint.route("/backpack/refresh", methods=["POST"])
def api_backpack_refresh():
    return jsonify(refresh_backpack())


@blueprint.route("/backpack/auto-decompose", methods=["GET"])
def api_backpack_auto_decompose_get():
    return jsonify(get_auto_decompose_s1_config())


@blueprint.route("/backpack/auto-decompose", methods=["PUT"])
def api_backpack_auto_decompose_put():
    return _json_ok(update_auto_decompose_s1_config(get_json_body()))


@blueprint.route("/item/use", methods=["POST"])
def api_item_use():
    return _json_ok(use_item(get_json_body()))


@blueprint.route("/item/drop", methods=["POST"])
def api_item_drop():
    return _json_ok(drop_item(get_json_body()))


@blueprint.route("/item/decompose", methods=["POST"])
def api_item_decompose():
    return _json_ok(decompose_item(get_json_body()))


@blueprint.route("/item/synthesize", methods=["POST"])
def api_item_synthesize():
    return _json_ok(synthesize_item(get_json_body()))


@blueprint.route("/item/exchange-wuling", methods=["POST"])
def api_exchange_wuling():
    return _json_ok(exchange_wuling())


@blueprint.route("/item/buy", methods=["POST"])
def api_item_buy():
    return _json_ok(buy_item(get_json_body()))
