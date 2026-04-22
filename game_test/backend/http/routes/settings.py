"""Persisted settings transport routes."""

from flask import Blueprint, jsonify

from ...application.settings_service import (
    get_buy_items,
    get_liaoguo_pairs,
    get_quick_logins,
    get_servers_catalog,
    remove_buy_item,
    remove_liaoguo_pair,
    remove_quick_login,
    save_buy_item,
    save_liaoguo_pair,
    save_quick_login,
)
from ..serializers import _json_ok
from ..validators import get_json_body


blueprint = Blueprint("http_settings", __name__, url_prefix="/api")


@blueprint.route("/servers", methods=["GET"])
def api_servers():
    return jsonify(get_servers_catalog())


@blueprint.route("/quick-logins", methods=["GET"])
def api_quick_logins_get():
    return jsonify(get_quick_logins())


@blueprint.route("/quick-logins", methods=["POST"])
def api_quick_logins_save():
    return _json_ok(save_quick_login(get_json_body()))


@blueprint.route("/quick-logins/<item_id>", methods=["DELETE"])
def api_quick_logins_delete(item_id: str):
    return jsonify(remove_quick_login(item_id))


@blueprint.route("/buy-items", methods=["GET"])
def api_buy_items_get():
    return jsonify(get_buy_items())


@blueprint.route("/buy-items", methods=["POST"])
def api_buy_items_save():
    return _json_ok(save_buy_item(get_json_body()))


@blueprint.route("/buy-items/<item_id>", methods=["DELETE"])
def api_buy_items_delete(item_id: str):
    return jsonify(remove_buy_item(item_id))


@blueprint.route("/liaoguo-pairs", methods=["GET"])
def api_liaoguo_pairs_get():
    return jsonify(get_liaoguo_pairs())


@blueprint.route("/liaoguo-pairs", methods=["POST"])
def api_liaoguo_pairs_save():
    return _json_ok(save_liaoguo_pair(get_json_body()))


@blueprint.route("/liaoguo-pairs/<item_id>", methods=["DELETE"])
def api_liaoguo_pairs_delete(item_id: str):
    return jsonify(remove_liaoguo_pair(item_id))
