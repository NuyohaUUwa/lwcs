"""
聊天业务协议。
只负责构造聊天报文。
"""


def build_chat_packet(message: str) -> str:
    """构造综合频道聊天报文。"""
    message_hex = message.encode("utf-8").hex()
    message_byte_len = len(message.encode("utf-8"))
    len_hex4 = f"{message_byte_len:04x}"
    message_len_hex = len_hex4[2:] + len_hex4[:2]
    header = "29000000e8030a00090470fef5051d040000170000000000000000000000"
    return header + message_len_hex + message_hex + "0000"
