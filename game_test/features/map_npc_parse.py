"""
地图 NPC 列表下行包解析（e80301004d4f / e8030100db07）。

规则与前端旧实现一致：
- 首段可为 [id:4][uint16=0][len:2 LE][UTF-8]（字节偏移 18）
- 后续为 [id:4][len:2 LE][UTF-8]，记录间有填充，在窗口内重扫对齐
- 链上对齐仅接受正文含 CJK 的 TLV，避免误把二进制碎片当成「短 UTF-8」
- 列表项：≤5 字且含 CJK；默认「当前」取第 1 个
"""

from __future__ import annotations

import binascii
import re
import sys
from typing import Dict, List, Optional, Tuple

MAP_NPC_FP_MARKERS = ("e80301004d4f", "e8030100db07")
MAP_NPC_PLAIN_MAX_SEEK = 64
MAP_NPC_PLAIN_MAX_LEN = 300
MAP_NPC_PADDED_INTRO_BYTE = 18


def _id_to_hex(id_bytes: bytes) -> str:
    return id_bytes.hex()


def bytes_from_hex(raw_hex: str) -> Optional[bytes]:
    h = str(raw_hex or "").lower().replace(" ", "").replace("\n", "").replace("\r", "").replace("\t", "")
    if not h or len(h) % 2 != 0:
        return None
    try:
        return binascii.unhexlify(h)
    except binascii.Error:
        return None


def is_map_npc_list_packet(*, direction: str, fingerprint: str, raw_hex: str) -> bool:
    if str(direction or "").upper() != "DN":
        return False
    fp = str(fingerprint or "").lower()
    raw = str(raw_hex or "").lower().replace(" ", "")
    head12 = raw[8:20] if len(raw) >= 20 else ""
    for m in MAP_NPC_FP_MARKERS:
        if m in fp or m in head12 or m in raw:
            return True
    return False


def _looks_like_plain_record(u8: bytes, j: int) -> bool:
    if j + 6 > len(u8):
        return False
    ln = u8[j + 4] | (u8[j + 5] << 8)
    if ln < 1 or ln > MAP_NPC_PLAIN_MAX_LEN or j + 6 + ln > len(u8):
        return False
    buf = u8[j + 6 : j + 6 + ln]
    if b"\x00" in buf:
        return False
    try:
        text = buf.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return False
    if "\ufffd" in text:
        return False
    # 必须与中文 NPC/地名一致；禁止仅用「≤6 字符」放行，否则会误对齐到二进制碎片（如 len=2 的 "{\x03"）
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def _find_next_plain_start(u8: bytes, start: int) -> Optional[int]:
    end = min(start + MAP_NPC_PLAIN_MAX_SEEK, len(u8) - 6)
    for j in range(start, end + 1):
        if _looks_like_plain_record(u8, j):
            return j
    return None


def _try_consume_padded_intro_at18(u8: bytes) -> Optional[Tuple[bytes, str, int]]:
    i = MAP_NPC_PADDED_INTRO_BYTE
    if i + 8 > len(u8):
        return None
    pad = u8[i + 4] | (u8[i + 5] << 8)
    ln = u8[i + 6] | (u8[i + 7] << 8)
    if pad != 0 or ln < 20 or ln > 4000 or i + 8 + ln > len(u8):
        return None
    id_b = u8[i : i + 4]
    text = u8[i + 8 : i + 8 + ln].decode("utf-8", errors="replace")
    return (id_b, text, i + 8 + ln)


def collect_map_npc_segments(u8: bytes) -> List[Tuple[bytes, str]]:
    segments: List[Tuple[bytes, str]] = []
    next_scan = 10
    padded = _try_consume_padded_intro_at18(u8)
    if padded:
        segments.append((padded[0], padded[1]))
        next_scan = padded[2]
    pos = _find_next_plain_start(u8, next_scan)
    guard = 0
    while pos is not None and pos + 6 <= len(u8) and guard < 400:
        guard += 1
        ln = u8[pos + 4] | (u8[pos + 5] << 8)
        id_b = u8[pos : pos + 4]
        text = u8[pos + 6 : pos + 6 + ln].decode("utf-8", errors="replace")
        segments.append((id_b, text))
        after = pos + 6 + ln
        npos = _find_next_plain_start(u8, after)
        if npos is None or npos <= pos:
            break
        pos = npos
    return segments


def _is_short_map_npc_label(utf8_text: str) -> bool:
    """短名列表：≤5 字且含 CJK（与游戏内 NPC/传送点名一致）。"""
    if len(utf8_text) > 5:
        return False
    if not re.search(r"[\u4e00-\u9fff]", utf8_text):
        return False
    for ch in utf8_text:
        if ord(ch) < 32 and ch not in "\t\n\r":
            return False
    return True


def extract_map_npc_list(raw_hex: str) -> List[Dict[str, str]]:
    """所有「短名」TLV 段（≤5 字且为中文标签），顺序与包内一致。"""
    u8 = bytes_from_hex(raw_hex)
    if not u8 or len(u8) < 24:
        return []
    try:
        segments = collect_map_npc_segments(u8)
    except (UnicodeDecodeError, IndexError, ValueError):
        return []
    out: List[Dict[str, str]] = []
    for id_b, utf8_text in segments:
        if not _is_short_map_npc_label(utf8_text):
            continue
        out.append({"id_hex": _id_to_hex(id_b), "utf8_text": utf8_text})
    return out


def extract_map_npc_hit(raw_hex: str) -> Optional[Dict[str, str]]:
    """兼容：等价于列表首项。"""
    lst = extract_map_npc_list(raw_hex)
    return lst[0] if lst else None


def compute_map_npc_list_for_packet(direction: str, fingerprint: str, raw_hex: str) -> List[Dict[str, str]]:
    if not is_map_npc_list_packet(direction=direction, fingerprint=fingerprint, raw_hex=raw_hex):
        return []
    return extract_map_npc_list(raw_hex)


def main(argv: Optional[List[str]] = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if args:
        raw = "".join(args)
    else:
        raw = sys.stdin.read()
    raw = raw.strip()
    if not raw:
        print("用法: python map_npc_parse.py <hex字符串>  或 管道 stdin 传入 hex", file=sys.stderr)
        return 2
    lst = extract_map_npc_list(raw)
    if not lst:
        print("null")
        return 1
    for row in lst:
        print(row["utf8_text"], row["id_hex"], sep="\t")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
