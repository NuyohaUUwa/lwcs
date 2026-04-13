"""
底层协议工具。
仅保留帧级解析、通用字段提取和文本抽取，不包含登录/角色/战斗等业务协议。
"""

import binascii
import re
import struct
from typing import Any, Dict, List, Optional, Tuple

# 与 services.action_manager._validate_packet_hex 一致：首 4 字节 LE 为 L，整包长度 = L + 4
_MAX_GAME_FRAME_BYTES = 512 * 1024


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


def split_game_frame_bytes(
    data: bytes,
    *,
    max_frame_bytes: int = _MAX_GAME_FRAME_BYTES,
) -> Tuple[List[bytes], bytes]:
    """
    将 TCP 缓冲区拆成若干条完整游戏报文，以及末尾可能不完整的半包。

    单条报文：前 4 字节小端无符号整数为 L，本条总字节数 = L + 4
    （与发包校验逻辑一致）。返回 (完整帧列表, 剩余待拼数据)。
    """
    frames: List[bytes] = []
    if not data:
        return frames, b""

    i = 0
    n = len(data)
    while i < n:
        if i + 4 > n:
            return frames, data[i:]
        length_field = int.from_bytes(data[i : i + 4], "little")
        total = length_field + 4
        if total < 4 or total > max_frame_bytes:
            # 无法对齐到合法长度头时单字节滑动，避免永久卡死
            i += 1
            continue
        if i + total > n:
            return frames, data[i:]
        frames.append(data[i : i + total])
        i += total
    return frames, b""


def slice_game_frame_hex_at(
    packet_hex: str,
    frame_start_hex_offset: int,
    *,
    max_frame_bytes: int = _MAX_GAME_FRAME_BYTES,
) -> Optional[str]:
    """
    从 hex 串的字符偏移 frame_start_hex_offset 起截取一条完整游戏帧（不含其后拼接的其它帧）。

    规则与 split_game_frame_bytes / _validate_packet_hex 一致：前 4 字节 LE 为 L，
    本条总字节数 = L + 4。长度非法或越界时返回 None。
    """
    h = len(packet_hex)
    if frame_start_hex_offset < 0 or frame_start_hex_offset + 8 > h:
        return None
    head_slice = packet_hex[frame_start_hex_offset : frame_start_hex_offset + 8]
    try:
        length_field = int.from_bytes(bytes.fromhex(head_slice), "little")
    except (ValueError, TypeError):
        return None
    total_bytes = length_field + 4
    if total_bytes < 4 or total_bytes > max_frame_bytes:
        return None
    end_hex = frame_start_hex_offset + total_bytes * 2
    if end_hex > h:
        return None
    return packet_hex[frame_start_hex_offset:end_hex]


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
