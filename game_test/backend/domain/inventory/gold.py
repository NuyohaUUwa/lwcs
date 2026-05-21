"""金币 / 金库 / 保险箱协议。"""

from __future__ import annotations

import re
from typing import Any

from game_test.backend.runtime import get_session

GOLD_REFRESH_PACKET = "1d000000e80313007d2eb7e7f505852e00000b00000009007a68656e66613f7b7d"
GOLD_DEPOSIT_PACKET = "2c000000e80313007d2e9ffef505942e00001a000000180073616665476f6c644465706f7369743130303030303f7b7d"
GOLD_WITHDRAW_PACKET = "2d000000e80313007d2e08eaf505952e00001b000000190073616665476f6c6457697468647261773130303030303f7b7d"
GOLD_STATUS_FINGERPRINT = "e80301008d55"

_GOLD_RE = re.compile(r"(\d+)\s*金(\d+)\s*银(\d+)\s*铜")
_GOLD_FIELDS = {
    b"cbName1": "safe",
    b"cbName2": "backpack",
    b"cbName": "reserve",
}


def format_copper(total_copper: int) -> str:
    total = max(0, int(total_copper or 0))
    gold = total // 10000
    silver = (total % 10000) // 100
    copper = total % 100
    return f"{gold}金{silver}银{copper}铜"


def parse_gold_text_to_copper(text: str) -> int | None:
    m = _GOLD_RE.search(str(text or "").strip())
    if not m:
        return None
    gold, silver, copper = (int(x) for x in m.groups())
    return gold * 10000 + silver * 100 + copper


def parse_gold_status_packet(packet_hex: str) -> dict[str, int] | None:
    try:
        packet_bytes = bytes.fromhex(str(packet_hex or "").strip())
    except ValueError:
        return None

    parsed: dict[str, int] = {}
    for key, field_name in _GOLD_FIELDS.items():
        key_offset = _find_field_offset(packet_bytes, key)
        if key_offset < 0:
            continue

        offset = key_offset + len(key)
        if offset + 2 > len(packet_bytes):
            continue
        title_len = int.from_bytes(packet_bytes[offset : offset + 2], "little")
        offset += 2 + title_len
        if offset + 2 > len(packet_bytes):
            continue
        value_len = int.from_bytes(packet_bytes[offset : offset + 2], "little")
        offset += 2
        end = offset + value_len
        if value_len <= 0 or end > len(packet_bytes):
            continue
        try:
            value_text = packet_bytes[offset:end].decode("utf-8")
        except UnicodeDecodeError:
            continue
        value = parse_gold_text_to_copper(value_text)
        if value is not None:
            parsed[field_name] = value

    return parsed or None


def _find_field_offset(packet_bytes: bytes, key: bytes) -> int:
    start = 0
    while True:
        pos = packet_bytes.find(key, start)
        if pos < 0:
            return -1
        end = pos + len(key)
        if key != b"cbName" or end >= len(packet_bytes) or packet_bytes[end] not in b"12":
            return pos
        start = end


def gold_snapshot_from_values(values: dict[str, int]) -> dict[str, Any]:
    reserve = int(values.get("reserve", 0) or 0)
    safe = int(values.get("safe", 0) or 0)
    backpack = int(values.get("backpack", 0) or 0)
    return {
        "reserve_copper": reserve,
        "safe_copper": safe,
        "backpack_copper": backpack,
        "reserve_text": format_copper(reserve),
        "safe_text": format_copper(safe),
        "backpack_text": format_copper(backpack),
    }


def handle_gold_status_packet(packet_hex: str) -> bool:
    parsed = parse_gold_status_packet(packet_hex)
    if not parsed:
        return False
    session = get_session()
    session.update_gold(parsed)
    session.notify_gold_update()
    return True
