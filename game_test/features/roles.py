"""
角色功能：
1. 连接游戏服 → 发送角色列表请求 → 解析角色列表（保持 socket 开着）
2. 选角 → 发送进游戏附加包 → 启动收包线程 + 发包工作线程
"""

import threading
import binascii

from core.codec import (
    generate_role_list_packet,
    parse_role_data,
    generate_select_role_packet,
    ENTER_GAME_EXTRA_PACKET,
    extract_packet_content,
)
from core.connector import connect, send_raw, start_receive_loop, start_send_worker
from core.session import get_session, RoleInfo
from config import SEND_INTERVAL
from features.packet_probe import record_packet
from features.heartbeat import start_heartbeat


def fetch_roles(server_ip: str, server_port: int) -> dict:
    """
    连接游戏服，获取角色列表。
    连接成功后 socket 保持开启，存入 GameSession.sock 供后续选角使用。

    Args:
        server_ip:   游戏服 IP 或域名
        server_port: 游戏服端口

    Returns:
        成功：{'ok': True, 'roles': [...]}
        失败：{'ok': False, 'error': '...'}
    """
    session = get_session()
    if not session.session_id:
        return {"ok": False, "error": "尚未登录，请先执行登录"}

    try:
        sock = connect(server_ip, server_port, timeout=15)
        packet_hex = generate_role_list_packet(session.session_id, server_ip, server_port)
        send_raw(sock, packet_hex)
        record_packet(packet_hex, "UP")

        response = sock.recv(10240)
        if not response:
            sock.close()
            return {"ok": False, "error": "游戏服未返回角色列表"}
        record_packet(response, "DN")

        response_hex = response.hex()
        response_str = response.decode('utf-8', errors='ignore')
        if '登录异常' in response_str or '请重新登录' in response_str:
            sock.close()
            return {"ok": False, "error": "登录异常，请重新登录"}

        role_data = parse_role_data(response_hex)

        roles: list = []
        for r in role_data.get('userList', []):
            roles.append(RoleInfo(
                role_id=r['role_id'],
                role_name=r['role_name_cn'],
                role_job=r['role_job'],
                role_index=r['role_index'],
            ))

        # 保存 socket 供后续选角
        session.sock = sock
        session.server_ip = server_ip
        session.server_port = server_port
        session.available_roles = roles
        session.notify_status_change()

        return {
            "ok": True,
            "roles": [r.to_dict() for r in roles],
        }

    except Exception as e:
        return {"ok": False, "error": f"获取角色列表失败: {e}"}


def select_role(role_id: str, on_packet_callback) -> dict:
    """
    选角并进入游戏，启动收包线程和发包工作线程。

    Args:
        role_id:             角色 ID（6位 hex）
        on_packet_callback:  收到下行包时的回调 (bytes -> None)，
                             通常为 api/server.py 的 dispatch_packet

    Returns:
        成功：{'ok': True, 'role': {...}}
        失败：{'ok': False, 'error': '...'}
    """
    session = get_session()
    if not session.sock:
        return {"ok": False, "error": "未连接游戏服，请先获取角色列表"}

    sock = session.sock
    try:
        sock.settimeout(5)

        # 发送选角包
        select_hex = generate_select_role_packet(role_id)
        send_raw(sock, select_hex)
        record_packet(select_hex, "UP")

        # 接收选角响应（含 d607 背包数据）
        response = sock.recv(14048)
        if response:
            record_packet(response, "DN")
            content = extract_packet_content(response.hex())
            text = content.get('cleaned_content', '')
            if '登录异常' in text or '请重新登录' in text:
                sock.close()
                session.sock = None
                return {"ok": False, "error": f"选角失败: {text}"}
            # 将选角响应分发给背包等解析模块（d607 背包数据就在此包中）
            on_packet_callback(response)

        # 发送进游戏后附加包
        send_raw(sock, ENTER_GAME_EXTRA_PACKET)
        record_packet(ENTER_GAME_EXTRA_PACKET, "UP")

        # 查找角色信息
        matched_role = next((r for r in session.available_roles if r.role_id == role_id), None)
        if not matched_role and session.available_roles:
            matched_role = session.available_roles[0]

        session.current_role = matched_role
        session.connected = True
        session.connection_status = "connected"

        # 启动收包线程
        stop_event = threading.Event()
        sock.settimeout(1)
        start_receive_loop(
            sock=sock,
            on_packet=on_packet_callback,
            on_error=lambda e: _on_disconnect(e),
            stop_event=stop_event,
        )

        # 启动发包工作线程
        send_lock = threading.Lock()
        start_send_worker(
            send_queue=session.send_queue,
            get_sock=lambda: session.sock if session.connected else None,
            send_lock=send_lock,
            stop_event=stop_event,
            interval=SEND_INTERVAL,
        )

        # 启动心跳线程（发keepalive + 检测超时断连）
        start_heartbeat(
            stop_event=stop_event,
            on_timeout=lambda: _on_disconnect(Exception("心跳超时：服务器长时间无响应")),
        )

        session.notify_status_change()
        return {
            "ok": True,
            "role": matched_role.to_dict() if matched_role else {"role_id": role_id},
        }

    except Exception as e:
        return {"ok": False, "error": f"选角失败: {e}"}


def _on_disconnect(error: Exception):
    """连接断开时清理 session 状态。防止心跳/recv_loop 同时触发时重复执行。"""
    session = get_session()
    if not session.connected:
        # 已经处理过断线，直接忽略（避免旧 recv_loop 在重连后再次触发）
        return
    session.connected = False
    session.connection_status = "disconnected"
    old_sock = session.sock
    session.sock = None
    # 关闭 socket，使 recv_loop 收到 socket.error 后退出并设置 stop_event
    if old_sock:
        try:
            old_sock.close()
        except Exception:
            pass
    session.notify_status_change()
    print(f"[roles] 游戏连接断开: {error}")
