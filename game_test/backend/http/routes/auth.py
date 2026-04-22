"""Authentication and session lifecycle transport routes."""

from flask import Blueprint, jsonify

from ...application.auth_flow_service import disconnect as app_disconnect
from ...application.auth_flow_service import execute_full_login_flow
from ...application.auth_flow_service import login as app_login
from ...application.role_service import fetch_roles, select_role
from ..serializers import _json_ok
from ..validators import get_int_field, get_json_body


blueprint = Blueprint("http_auth", __name__, url_prefix="/api")


@blueprint.route("/login", methods=["POST"])
def api_login():
    body = get_json_body()
    account = body.get("account", "").strip()
    password = body.get("password", "").strip()
    server = body.get("server", "").strip()
    if not account or not password or not server:
        return jsonify({"ok": False, "error": "account / password / server 不能为空"}), 400
    return _json_ok(app_login(account, password, server))


@blueprint.route("/login/full", methods=["POST"])
def api_login_full():
    body = get_json_body()
    account = body.get("account", "").strip()
    password = body.get("password", "").strip()
    server = body.get("server", "").strip()
    login_server = body.get("login_server", "").strip()
    server_ip = body.get("server_ip", "").strip()
    try:
        server_port = get_int_field(body, "server_port", 0)
        max_attempts = max(1, min(10, get_int_field(body, "max_attempts", 1)))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    role_id = body.get("role_id", "").strip()
    require_role_stats = bool(body.get("require_role_stats", True))
    if not account or not password or not login_server or not server_ip or not server_port or not role_id:
        return jsonify({"ok": False, "error": "缺少完整登录上下文"}), 400
    return _json_ok(execute_full_login_flow(
        account, password, server, login_server,
        server_ip, server_port, role_id,
        require_role_stats=require_role_stats,
        max_attempts=max_attempts,
    ))


@blueprint.route("/roles", methods=["POST"])
def api_roles():
    body = get_json_body()
    server_name = body.get("server_name", "").strip()
    server_ip = body.get("server_ip", "").strip()
    try:
        server_port = get_int_field(body, "server_port", 0)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return _json_ok(fetch_roles(server_ip=server_ip, server_port=server_port, server_name=server_name))


@blueprint.route("/select-role", methods=["POST"])
def api_select_role():
    body = get_json_body()
    role_id = body.get("role_id", "").strip()
    if not role_id:
        return jsonify({"ok": False, "error": "role_id 不能为空"}), 400
    return _json_ok(select_role(role_id))


@blueprint.route("/disconnect", methods=["POST"])
def api_disconnect():
    return jsonify(app_disconnect())
