"""
登录功能：
1. 连接登录服 → 发送登录包 → 提取 session_id → 关闭登录连接
2. 将 session_id 写入 GameSession
3. 解析登录响应中的公告 + 服务器列表
"""

import socket
import binascii

from core.codec import (
    generate_login_packet,
    get_session_id_hex,
    extract_packet_content,
)
from core.session import get_session
from config import LOGIN_SERVERS
from features.packet_probe import record_packet


def do_login(account: str, password: str, server_name: str) -> dict:
    """
    执行登录流程。

    Args:
        account:     游戏账号（ASCII）
        password:    游戏密码（ASCII）
        server_name: 服务器名称，须为 config.LOGIN_SERVERS 中的键

    Returns:
        成功：{'ok': True, 'session_id': '...', 'announcement': '...', 'server_list': [...]}
        失败：{'ok': False, 'error': '...'}
    """
    session = get_session()

    if server_name not in LOGIN_SERVERS:
        return {"ok": False, "error": f"未知服务器: {server_name}"}

    srv = LOGIN_SERVERS[server_name]
    ip, port = srv["ip"], srv["port"]

    sock = None
    try:
        session.connection_status = "connecting"
        session.notify_status_change()

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(15)
        sock.connect((ip, port))

        packet_hex = generate_login_packet(account, password)
        sock.send(binascii.unhexlify(packet_hex))
        record_packet(packet_hex, "UP")

        response = sock.recv(4096)
        if not response:
            return {"ok": False, "error": "登录服无响应"}
        record_packet(response, "DN")

        session_id = get_session_id_hex(response)

        parsed = extract_packet_content(response.hex())
        announcement = parsed.get("cleaned_content", "")
        server_list = parsed.get("server_list", [])

        # 写入全局会话
        session.session_id = session_id
        session.account = account
        session.server_name = server_name
        session.announcement = announcement
        session.server_list = server_list
        session.connection_status = "got_session"
        session.notify_status_change()

        return {
            "ok": True,
            "session_id": session_id,
            "announcement": announcement,
            "server_list": server_list,
        }

    except RuntimeError as e:
        return {"ok": False, "error": str(e)}
    except socket.timeout:
        return {"ok": False, "error": "登录超时，请检查网络或服务器地址"}
    except ConnectionRefusedError:
        return {"ok": False, "error": f"连接被拒绝: {ip}:{port}"}
    except Exception as e:
        return {"ok": False, "error": f"登录异常: {e}"}
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass
