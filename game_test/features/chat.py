"""
聊天功能：发送综合频道消息。
直接通过 socket 发送（不走优先队列），与 main-000.py 保持一致。
"""

import binascii

from core.session import get_session


def send_chat(message: str) -> dict:
    """
    发送综合频道聊天消息。

    报文格式：
      固定头（29字节 hex）+ 消息长度（2字节小端 hex）+ 消息 UTF-8 hex + 0000

    Args:
        message: 要发送的文本内容

    Returns:
        成功：{'ok': True, 'sent_bytes': int}
        失败：{'ok': False, 'error': str}
    """
    session = get_session()
    if not session.connected or not session.sock:
        return {"ok": False, "error": "未连接游戏服"}
    if not message.strip():
        return {"ok": False, "error": "消息内容不能为空"}

    try:
        message_hex = message.encode('utf-8').hex()
        message_byte_len = len(message.encode('utf-8'))
        # 小端序 2 字节长度：先转 4 位 hex，再字节交换
        len_hex4 = f"{message_byte_len:04x}"
        message_len_hex = len_hex4[2:] + len_hex4[:2]

        header = "29000000e8030a00090470fef5051d040000170000000000000000000000"
        packet_hex = header + message_len_hex + message_hex + "0000"
        packet = binascii.unhexlify(packet_hex)

        sent = session.sock.send(packet)
        return {"ok": True, "sent_bytes": sent}

    except Exception as e:
        return {"ok": False, "error": f"发送聊天消息失败: {e}"}
