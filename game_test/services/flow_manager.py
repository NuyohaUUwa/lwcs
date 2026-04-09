"""
统一流程调度层。
负责登录、拉角色、选角、断开连接，以及统一下行分发。
"""

from typing import Callable, Dict

from config import GAME_SERVERS, LOGIN_SERVERS
from core.connector import connect_and_exchange, open_connection, start_connection_runtime, stop_connection_runtime
from core.session import RoleInfo, get_session
from features.backpack import dispatch_backpack_packet
from features.battle import evaluate_auto_use, parse_battle_end, parse_battle_response
from features.heartbeat import start_heartbeat
from features.login import build_login_packet, parse_login_response
from features.packet_probe import record_packet
from features.role_stats import update_session_stats
from features.roles import (
    ENTER_GAME_EXTRA_PACKET,
    build_role_list_packet,
    build_select_role_packet,
    parse_role_data,
    parse_select_role_response,
)


def _set_status(status: str):
    session = get_session()
    session.connection_status = status
    session.notify_status_change()


def _default_disconnect_handler(error: Exception):
    session = get_session()
    if not session.connected and session.connection_status == "disconnected":
        return
    print(f"[flow] 游戏连接断开: {error}")
    session.connected = False
    session.connection_status = "disconnected"
    session.stop_runtime()
    stop_connection_runtime()
    session.clear_connection_runtime()
    session.notify_status_change()


def handle_incoming_packet(raw_bytes: bytes) -> None:
    """统一下行分发入口。"""
    if not raw_bytes:
        return

    session = get_session()
    hex_str = raw_bytes.hex()
    session.last_recv_ts = __import__("time").time()
    record_packet(hex_str, "DN")

    fingerprint = hex_str[8:20] if len(hex_str) >= 20 else ""
    if "d607" in fingerprint:
        dispatch_backpack_packet(hex_str)
        update_session_stats(hex_str)
        evaluate_auto_use("d607")
        return
    if "de07" in fingerprint:
        parse_battle_response(hex_str)
        return
    if "df07" in fingerprint:
        parse_battle_end(hex_str)
        evaluate_auto_use("df07")
        return
    dispatch_backpack_packet(hex_str)


def login_flow(account: str, password: str, server_name: str) -> dict:
    """登录流程。"""
    session = get_session()
    if server_name not in LOGIN_SERVERS:
        return {"ok": False, "error": f"未知服务器: {server_name}"}

    srv = LOGIN_SERVERS[server_name]
    _set_status("connecting")
    packet_hex = build_login_packet(account, password)

    try:
        record_packet(packet_hex, "UP")
        response = connect_and_exchange(
            srv["ip"],
            srv["port"],
            packet_hex,
            connect_timeout=15,
            recv_timeout=15,
            bufsize=4096,
            keep_open=False,
        )
        if not response:
            return {"ok": False, "error": "登录服无响应"}
        record_packet(response, "DN")
        parsed = parse_login_response(response)
        session.session_id = parsed["session_id"]
        session.account = account
        session.server_name = server_name
        session.announcement = parsed["announcement"]
        session.server_list = parsed["server_list"]
        _set_status("got_session")
        return {
            "ok": True,
            "session_id": parsed["session_id"],
            "announcement": parsed["announcement"],
            "server_list": parsed["server_list"],
        }
    except Exception as e:
        _set_status("disconnected")
        return {"ok": False, "error": f"登录异常: {e}"}


def fetch_roles_flow(server_ip: str = "", server_port: int = 0, server_name: str = "") -> dict:
    """获取角色列表并保留游戏服连接。"""
    session = get_session()
    if not session.session_id:
        return {"ok": False, "error": "尚未登录，请先执行登录"}

    if server_name and server_name in GAME_SERVERS:
        srv = GAME_SERVERS[server_name]
        server_ip = srv["ip"]
        server_port = srv["port"]
        session.server_name = server_name
    if not server_ip or not server_port:
        return {"ok": False, "error": "需要提供 server_ip + server_port 或 server_name"}

    try:
        packet_hex = build_role_list_packet(session.session_id, server_ip, int(server_port))
        open_connection(server_ip, int(server_port), timeout=15)
        record_packet(packet_hex, "UP")
        from core.connector import send_and_receive_once

        response = send_and_receive_once(packet_hex, recv_timeout=5, bufsize=10240)
        if not response:
            return {"ok": False, "error": "游戏服未返回角色列表"}
        record_packet(response, "DN")
        response_str = response.decode("utf-8", errors="ignore")
        if "登录异常" in response_str or "请重新登录" in response_str:
            stop_connection_runtime()
            return {"ok": False, "error": "登录异常，请重新登录"}

        roles = []
        role_data = parse_role_data(response.hex())
        for r in role_data.get("userList", []):
            roles.append(
                RoleInfo(
                    role_id=r["role_id"],
                    role_name=r["role_name_cn"],
                    role_job=r["role_job"],
                    role_index=r["role_index"],
                )
            )
        session.available_roles = roles
        session.server_ip = server_ip
        session.server_port = int(server_port)
        session.notify_status_change()
        return {"ok": True, "roles": [r.to_dict() for r in roles]}
    except Exception as e:
        stop_connection_runtime()
        return {"ok": False, "error": f"获取角色列表失败: {e}"}


def select_role_flow(role_id: str) -> dict:
    """选角并进入游戏。"""
    session = get_session()
    if not session.sock:
        return {"ok": False, "error": "未连接游戏服，请先获取角色列表"}

    try:
        select_hex = build_select_role_packet(role_id)
        from services.action_manager import send_and_wait, send_raw_action

        select_res = send_and_wait(select_hex, timeout=5)
        if not select_res.get("ok"):
            return {"ok": False, "error": f"选角失败: {select_res.get('error')}"}

        parsed = parse_select_role_response(select_res["response_bytes"])
        if not parsed["ok"]:
            stop_connection_runtime()
            return {"ok": False, "error": f"选角失败: {parsed['text']}"}

        handle_incoming_packet(select_res["response_bytes"])

        extra_res = send_raw_action(ENTER_GAME_EXTRA_PACKET, priority=10, use_queue=False)
        if not extra_res.get("ok"):
            return extra_res

        matched_role = next((r for r in session.available_roles if r.role_id == role_id), None)
        if not matched_role and session.available_roles:
            matched_role = session.available_roles[0]
        session.current_role = matched_role
        session.connected = True
        _set_status("connected")

        runtime = start_connection_runtime(handle_incoming_packet, _default_disconnect_handler)
        heartbeat_thread = start_heartbeat(
            runtime["stop_event"],
            on_timeout=lambda: _default_disconnect_handler(Exception("心跳超时：服务器长时间无响应")),
        )
        session.heartbeat_thread = heartbeat_thread
        return {"ok": True, "role": matched_role.to_dict() if matched_role else {"role_id": role_id}}
    except Exception as e:
        _default_disconnect_handler(e)
        return {"ok": False, "error": f"选角失败: {e}"}


def disconnect_flow() -> dict:
    """断开当前连接。"""
    session = get_session()
    session.reset()
    return {"ok": True, "message": "已断开连接"}
