"""
统一流程调度层。
负责登录、拉角色、选角、断开连接，以及统一下行分发。
"""

import threading
import time
from typing import Callable, Dict

from config import GAME_SERVERS, LOGIN_SERVERS
from core.connector import connect_and_exchange, open_connection, start_connection_runtime, stop_connection_runtime
from core.session import RoleInfo, get_session
from features.backpack import dispatch_backpack_packet
from features.battle import (
    BATTLE_STATE_ENDED,
    BATTLE_STATE_WAITING_ACTION_RESULT,
    BATTLE_STATE_WAITING_START_RESPONSE,
    MAX_F703_TIMEOUT_RECOVER,
    clear_battle_wait_deadline,
    get_battle_state_snapshot,
    get_wait_timeout_reason,
    handle_battle_server_packet,
    is_battle_wait_timed_out,
    recover_battle_wait_timeout_with_f703,
    reset_battle_state,
    schedule_loop_restart_after_reconnect,
    start_loop_battle_round,
    stop_battle_loop,
)
from features.heartbeat import start_heartbeat
from features.login import build_login_packet, parse_login_response
from features.role_stats import update_session_stats
from features.roles import (
    build_enter_game_extra_packet,
    build_role_list_packet,
    build_select_role_packet,
    parse_role_data,
    parse_select_role_response,
)

_CONTROL_LOOP_SLEEP_S = 0.2
_BANNED_ROLE_HEX_TOKEN = "e8afa5e8a792e889b2e5b7b2e8a2abe7a681e5b081"
_BANNED_ROLE_TEXT = "该角色已被禁封"


def _emit_control_log(message: str, *, level: str = "info", scope: str = "reconnect", **extra):
    session = get_session()
    payload = {"scope": scope, "level": level, "message": message}
    if extra:
        payload.update(extra)
    session._notify_sse("control_log", payload)


def _has_reconnect_context(session) -> bool:
    return bool(
        session.account
        and session.login_password
        and session.login_server_name
        and session.server_ip
        and session.server_port
        and session.reconnect_role_id
    )


def ensure_control_worker_running():
    session = get_session()
    with session._lock:
        if session.control_thread and session.control_thread.is_alive():
            return

        thread = threading.Thread(target=_control_worker_loop, daemon=True, name="backend-control-loop")
        session.control_thread = thread
        thread.start()


def _update_reconnect_state(
    state: str,
    *,
    reason: str | None = None,
    attempts: int | None = None,
    last_error: str | None = None,
    next_retry_ts: float | None = None,
    banned_until_ts: float | None = None,
):
    session = get_session()
    with session._lock:
        session.reconnect_state = state
        if reason is not None:
            session.reconnect_reason = reason
        if attempts is not None:
            session.reconnect_attempts = attempts
        if last_error is not None:
            session.reconnect_last_error = last_error
        if next_retry_ts is not None:
            session.reconnect_next_retry_ts = next_retry_ts
        if banned_until_ts is not None:
            session.reconnect_banned_until_ts = banned_until_ts
    session.notify_control_state()
    session.notify_status_change()


def set_auto_reconnect_enabled(enabled: bool) -> dict:
    ensure_control_worker_running()
    session = get_session()
    with session._lock:
        session.auto_reconnect_enabled = bool(enabled)
    session.notify_control_state()
    session.notify_status_change()
    return {"ok": True, "control_state": session.get_control_state()}


def cancel_pending_reconnect(reason: str = "") -> dict:
    session = get_session()
    with session._lock:
        if session.reconnect_state not in ("scheduled", "failed", "idle"):
            return {"ok": False, "control_state": session.get_control_state()}
        session.reconnect_state = "idle"
        session.reconnect_reason = ""
        session.reconnect_attempts = 0
        session.reconnect_last_error = ""
        session.reconnect_next_retry_ts = 0.0
        session.reconnect_banned_until_ts = 0.0
    session.notify_control_state()
    session.notify_status_change()
    if reason:
        _emit_control_log(reason, level="info", scope="reconnect")
    return {"ok": True, "control_state": session.get_control_state()}


def _schedule_reconnect(reason: str, *, immediate: bool = False, delay_s: float | None = None):
    ensure_control_worker_running()
    session = get_session()
    with session._lock:
        if not session.auto_reconnect_enabled:
            return False
        if not _has_reconnect_context(session):
            _emit_control_log("缺少后端重连上下文，无法自动恢复", level="warn")
            return False
        current_state = session.reconnect_state
        if current_state == "running":
            return True
        attempts = session.reconnect_attempts
        if delay_s is None:
            delay_s = 0.0 if immediate else min(3 * max(1, attempts + 1), 30)
        next_retry_ts = time.time() + max(0.0, delay_s)
        session.reconnect_state = "scheduled"
        session.reconnect_reason = reason
        session.reconnect_last_error = ""
        session.reconnect_next_retry_ts = next_retry_ts
        if delay_s <= 0:
            session.reconnect_banned_until_ts = 0.0
    session.notify_control_state()
    session.notify_status_change()
    return True


def _should_keep_reconnecting(session) -> bool:
    return bool(session.auto_reconnect_enabled)


def _get_retry_delay_s(attempts: int) -> float:
    if attempts <= 1:
        return 0.0
    if attempts == 2:
        return 1.0
    return 2.0


def _schedule_banned_reconnect(reason: str, *, delay_s: float):
    ensure_control_worker_running()
    session = get_session()
    with session._lock:
        if not _has_reconnect_context(session):
            return False
        banned_until = time.time() + max(0.0, delay_s)
        session.reconnect_state = "banned_wait"
        session.reconnect_reason = reason
        session.reconnect_next_retry_ts = banned_until
        session.reconnect_banned_until_ts = banned_until
    session.notify_control_state()
    session.notify_status_change()
    return True


def _perform_backend_reconnect():
    session = get_session()
    with session._lock:
        if not _has_reconnect_context(session):
            return
        attempts = session.reconnect_attempts + 1
        session.reconnect_attempts = attempts
        session.reconnect_state = "running"
        session.reconnect_last_error = ""
        session.reconnect_next_retry_ts = 0.0
        account = str(session.account or "")
        password = str(session.login_password or "")
        login_server = str(session.login_server_name or "")
        server_ip = str(session.server_ip or "")
        server_port = int(session.server_port or 0)
        server_name = str(session.server_name or "")
        role_id = str(session.reconnect_role_id or "")
    session.notify_control_state()
    session.notify_status_change()
    _emit_control_log(f"后端开始自动重连，第 {attempts} 次尝试", scope="reconnect")

    flow_res = login_flow(account, password, login_server)
    if flow_res.get("ok"):
        flow_res = fetch_roles_flow(server_ip=server_ip, server_port=server_port, server_name=server_name)
    if flow_res.get("ok"):
        flow_res = select_role_flow(role_id)

    if flow_res.get("ok"):
        _update_reconnect_state(
            "idle",
            reason="",
            attempts=0,
            last_error="",
            next_retry_ts=0.0,
            banned_until_ts=0.0,
        )
        _emit_control_log("后端自动重连成功", level="ok", scope="reconnect")
        if get_battle_state_snapshot().get("loop_running"):
            schedule_loop_restart_after_reconnect(0.3)
            _emit_control_log("后端将恢复循环战斗", level="ok", scope="battle")
        return

    error = str(flow_res.get("error") or "未知错误")
    with session._lock:
        attempts = session.reconnect_attempts
        max_attempts = session.reconnect_max_attempts
        loop_running = session.battle_loop_running
    if attempts >= max_attempts:
        _update_reconnect_state("failed", last_error=error, next_retry_ts=0.0)
        _emit_control_log(f"自动重连失败，已达到最大重试次数：{error}", level="warn", scope="reconnect")
        return

    delay_s = _get_retry_delay_s(attempts + 1)
    _update_reconnect_state("scheduled", last_error=error, next_retry_ts=time.time() + delay_s)
    _emit_control_log(f"自动重连失败：{error}；{delay_s:.1f} 秒后重试", level="warn", scope="reconnect")


def _control_worker_tick(now: float) -> None:
    session = get_session()
    battle_state = get_battle_state_snapshot()
    control_state = session.get_control_state()

    if control_state.get("reconnect_state") in ("scheduled", "banned_wait"):
        next_retry_ts = float(control_state.get("reconnect_next_retry_ts") or 0.0)
        if not _should_keep_reconnecting(session):
            cancel_pending_reconnect("后端检测到未开启自动重连，取消重连任务")
            control_state = session.get_control_state()
            next_retry_ts = 0.0
        if next_retry_ts > 0 and now >= next_retry_ts and not session.connected:
            _perform_backend_reconnect()

    if session.connected and is_battle_wait_timed_out(now):
        reason = get_wait_timeout_reason()
        clear_battle_wait_deadline()
        res = recover_battle_wait_timeout_with_f703()
        if res.get("ok"):
            n = res.get("recover_count", 0)
            _emit_control_log(
                f"{reason}，已发送 f703 超时恢复（第 {n}/{MAX_F703_TIMEOUT_RECOVER} 次）",
                level="warn",
                scope="battle",
            )
        else:
            _emit_control_log(
                f"{reason}，超时恢复未继续：{res.get('error', '')}",
                level="warn",
                scope="battle",
            )

    next_start_ts = float(battle_state.get("next_start_ts") or 0.0)
    if (
        session.connected
        and battle_state.get("loop_running")
        and battle_state.get("state") in (BATTLE_STATE_ENDED, "idle")
        and next_start_ts > 0
        and now >= next_start_ts
        and control_state.get("reconnect_state") == "idle"
    ):
        with session._lock:
            session.battle_next_start_ts = 0.0
            monster_code = session.battle_loop_monster_code or session.battle_current_monster
        session.notify_battle_state()
        if monster_code:
            # 上一轮战斗结束时已 prepare 的自动使用，需在本轮 f603 前执行（run_pending_auto_use_actions）
            res = start_loop_battle_round(monster_code, run_pre_battle_actions=True)
            if not res.get("ok"):
                error = res.get("error") or "下一轮战斗启动失败"
                _emit_control_log(
                    f"后端启动下一轮战斗失败：{error}",
                    level="warn",
                    scope="battle",
                )
                if session.connected:
                    _default_disconnect_handler(Exception(error))
                elif session.auto_reconnect_enabled:
                    _schedule_reconnect(error, immediate=True)


def _control_worker_loop():
    while True:
        try:
            _control_worker_tick(time.time())
        except Exception as e:
            print(f"[flow] backend control loop error: {e}")
        time.sleep(_CONTROL_LOOP_SLEEP_S)


def _decode_packet_text(packet_hex: str) -> str:
    try:
        raw = bytes.fromhex(packet_hex[16:] if len(packet_hex) > 16 else packet_hex)
    except Exception:
        return ""
    return raw.decode("utf-8", errors="ignore")


def _is_banned_role_packet(packet_hex: str) -> bool:
    fingerprint = packet_hex[8:20] if len(packet_hex) >= 20 else ""
    if "d607" not in fingerprint:
        return False
    text = _decode_packet_text(packet_hex)
    return _BANNED_ROLE_HEX_TOKEN in packet_hex.lower() or _BANNED_ROLE_TEXT in text


def _handle_banned_role_packet():
    if not _schedule_banned_reconnect("该角色已被禁封", delay_s=10 * 60):
        return
    session = get_session()
    _emit_control_log("检测到角色禁封，后端将在 10 分钟后自动重连", level="warn", scope="reconnect")
    reset_battle_state(preserve_loop=True)
    with session._lock:
        session.connected = False
        session.connection_status = "disconnected"
    session.stop_runtime()
    stop_connection_runtime()
    session.clear_connection_runtime()
    session.notify_status_change()
    session.notify_battle_state()


def _set_status(status: str):
    ensure_control_worker_running()
    session = get_session()
    session.connection_status = status
    session.notify_status_change()


def _default_disconnect_handler(error: Exception):
    session = get_session()
    if not session.connected and session.connection_status == "disconnected":
        return
    print(f"[flow] 游戏连接断开: {error}")
    preserve_loop = bool(session.battle_loop_running)
    reset_battle_state(preserve_loop=preserve_loop)
    with session._lock:
        session.role_stats = {}
    session.connected = False
    session.connection_status = "disconnected"
    session.stop_runtime()
    stop_connection_runtime()
    session.clear_connection_runtime()
    session.notify_status_change()
    if session.auto_reconnect_enabled:
        if _schedule_reconnect(str(error), immediate=True):
            _emit_control_log(f"连接断开：{error}；后端将自动恢复", level="warn", scope="reconnect")


def handle_incoming_packet(raw_bytes: bytes) -> None:
    """统一下行分发入口。"""
    if not raw_bytes:
        return

    session = get_session()
    hex_str = raw_bytes.hex()
    session.last_recv_ts = __import__("time").time()

    fingerprint = hex_str[8:20] if len(hex_str) >= 20 else ""
    if _is_banned_role_packet(hex_str):
        _handle_banned_role_packet()
        return
    if "d607" in fingerprint:
        dispatch_backpack_packet(hex_str)
        update_session_stats(hex_str)
        return
    if "de07" in fingerprint:
        handle_battle_server_packet(hex_str)
        return
    if "df07" in fingerprint:
        handle_battle_server_packet(hex_str)
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
        parsed = parse_login_response(response)
        session.session_id = parsed["session_id"]
        session.account = account
        session.login_password = password
        session.login_server_name = server_name
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
        ensure_control_worker_running()
        packet_hex = build_role_list_packet(session.session_id, server_ip, int(server_port))
        open_connection(server_ip, int(server_port), timeout=15)
        from core.connector import send_and_receive_once

        response = send_and_receive_once(packet_hex, recv_timeout=5, bufsize=10240)
        if not response:
            return {"ok": False, "error": "游戏服未返回角色列表"}
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
        if server_name:
            session.server_name = server_name
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
        ensure_control_worker_running()
        with session._lock:
            session.role_stats = {}
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
        with session._lock:
            role_stats_ready = bool(session.role_stats)
        if not role_stats_ready:
            stop_connection_runtime()
            return {"ok": False, "error": "选角成功但未收到角色属性，重连判定失败"}

        extra_res = send_raw_action(build_enter_game_extra_packet(), priority=10, use_queue=False)
        if not extra_res.get("ok"):
            return extra_res

        matched_role = next((r for r in session.available_roles if r.role_id == role_id), None)
        if not matched_role and session.available_roles:
            matched_role = session.available_roles[0]
        session.current_role = matched_role
        session.reconnect_role_id = role_id
        session.connected = True
        session.reconnect_state = "idle"
        session.reconnect_reason = ""
        session.reconnect_last_error = ""
        session.reconnect_next_retry_ts = 0.0
        session.reconnect_banned_until_ts = 0.0
        _set_status("connected")

        runtime = start_connection_runtime(handle_incoming_packet, _default_disconnect_handler)
        heartbeat_thread = start_heartbeat(
            runtime["stop_event"],
            on_timeout=lambda: _default_disconnect_handler(Exception("心跳超时：服务器长时间无响应")),
        )
        session.heartbeat_thread = heartbeat_thread
        session.notify_control_state()
        return {"ok": True, "role": matched_role.to_dict() if matched_role else {"role_id": role_id}}
    except Exception as e:
        _default_disconnect_handler(e)
        return {"ok": False, "error": f"选角失败: {e}"}


def disconnect_flow() -> dict:
    """断开当前连接。"""
    session = get_session()
    if session.battle_loop_running:
        session.connected = False
        session.connection_status = "disconnected"
        session.stop_runtime()
        stop_connection_runtime()
        session.clear_connection_runtime()
        session.notify_status_change()
        if session.auto_reconnect_enabled:
            _schedule_reconnect("循环战斗中手动断开，保持循环并自动重连", immediate=True)
            _emit_control_log("循环战斗中手动断开：后端保持循环并将自动重连", level="warn", scope="reconnect")
            return {"ok": True, "message": "已断开连接，循环战斗保持开启，后端将自动重连"}
        _emit_control_log(
            "循环战斗中手动断开：循环意图已保留，未开启自动重连故不会自动重连",
            level="info",
            scope="battle",
        )
        return {"ok": True, "message": "已断开连接，循环战斗保持开启（未开启自动重连，不会自动重连）"}
    reset_battle_state()
    session.reset()
    return {"ok": True, "message": "已断开连接"}
