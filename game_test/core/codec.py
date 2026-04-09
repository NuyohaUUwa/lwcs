"""
底层协议工具。
仅保留帧级解析、通用字段提取和文本抽取，不包含登录/角色/战斗等业务协议。
"""

import binascii
import re
import struct
from typing import Any, Dict, List


def find_all_positions(text: str, pattern: str) -> List[int]:
    """在 hex 字符串中查找所有 pattern 出现位置。"""
    positions = []
    start = 0
    while True:
        pos = text.find(pattern, start)
        if pos == -1:
            break
        positions.append(pos)
        start = pos + 1
    return positions


def extract_utf8_segments(hex_str: str, min_len: int = 3) -> str:
    """从 hex 字符串正文区提取可读 UTF-8 内容。"""
    try:
        body_bytes = bytes.fromhex(hex_str[16:])
    except (ValueError, binascii.Error):
        return ""

    result_parts: List[str] = []
    i = 0
    n = len(body_bytes)

    while i < n:
        decoded_len = 0
        buf = bytearray()
        j = i
        while j < n:
            b = body_bytes[j]
            if b < 0x80:
                seq_len = 1
            elif b < 0xC0:
                break
            elif b < 0xE0:
                seq_len = 2
            elif b < 0xF0:
                seq_len = 3
            else:
                seq_len = 4

            if j + seq_len > n:
                break

            seq = body_bytes[j : j + seq_len]
            try:
                seq.decode("utf-8")
                buf += seq
                j += seq_len
                decoded_len += seq_len
            except UnicodeDecodeError:
                break

        if decoded_len >= min_len:
            text = buf.decode("utf-8", errors="replace")
            text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
            if text.strip():
                result_parts.append(text)
            i = j
        else:
            result_parts.append(f"[{body_bytes[i]:02X}]")
            i += 1

    return "".join(result_parts)


def extract_packet_fingerprint(packet_hex: str) -> str:
    """返回报文指纹：hex[8:20]。"""
    return packet_hex[8:20] if len(packet_hex) >= 20 else packet_hex


def parse_frame(hex_data: str) -> Dict[str, Any]:
    """解析通用帧头与正文信息。"""
    try:
        byte_data = bytes.fromhex(hex_data)
        if len(byte_data) < 8:
            return {"error": "报文长度不足8字节"}

        content_length = struct.unpack("<I", byte_data[0:4])[0]
        command = struct.unpack("<I", byte_data[4:8])[0]
        actual_content = byte_data[8 : 8 + content_length] if len(byte_data) >= 8 + content_length else byte_data[8:]
        decoded = actual_content.decode("utf-8", errors="ignore")
        cleaned = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", decoded)
        return {
            "content_length": content_length,
            "command": command,
            "command_hex": f"0x{command:08X}",
            "cleaned_content": cleaned.strip(),
            "raw_decoded": decoded,
        }
    except Exception as e:
        return {"error": f"解析失败: {e}"}
