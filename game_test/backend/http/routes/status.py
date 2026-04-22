"""Status and control transport routes."""

from flask import Blueprint, jsonify

from ...application.role_service import get_role_stats_snapshot
from ...application.session_status_service import (
    get_control_state_snapshot,
    get_status_snapshot,
    update_control_config,
)
from ..serializers import _json_ok
from ..validators import get_json_body


blueprint = Blueprint("http_status", __name__, url_prefix="/api")


@blueprint.route("/status", methods=["GET"])
def api_status():
    return jsonify(get_status_snapshot())


@blueprint.route("/control-state", methods=["GET"])
def api_control_state():
    return jsonify(get_control_state_snapshot())


@blueprint.route("/control-config", methods=["PUT"])
def api_control_config():
    body = get_json_body()
    return _json_ok(update_control_config(auto_reconnect=bool(body.get("auto_reconnect", False))))


@blueprint.route("/role-stats", methods=["GET"])
def api_role_stats():
    return jsonify(get_role_stats_snapshot())
