"""
物品业务协议。
负责构造业务报文与执行乐观状态更新，不直接发包。
"""

from core.session import get_session
from utils.random_num import random_num_hex4, random_num_hex6


def _normalize_hex(value: str, expected_len: int, name: str) -> str:
    clean = str(value or "").strip().lower()
    if len(clean) != expected_len:
        raise ValueError(f"{name} 必须是 {expected_len} 位 hex")
    try:
        int(clean, 16)
    except ValueError as e:
        raise ValueError(f"{name} 不是合法 hex") from e
    return clean


def _normalize_hex_even_range(value: str, min_len: int, max_len: int, name: str) -> str:
    clean = str(value or "").strip().lower()
    if len(clean) < min_len or len(clean) > max_len or len(clean) % 2 != 0:
        raise ValueError(f"{name} 必须是 {min_len}~{max_len} 位且偶数长度的 hex")
    try:
        int(clean, 16)
    except ValueError as e:
        raise ValueError(f"{name} 不是合法 hex") from e
    return clean


def build_use_item_packets(item_id: str, quantity: int = 1) -> list[str]:
    packets = []
    for _ in range(quantity):
        random_num = random_num_hex6()
        packets.append(
            "1e000000e80308000404" + random_num + "050d0400000c00000000" + item_id + "0000010000"
        )
    return packets


def build_use_item_packets_for_item_code(item_code: str, quantity: int = 1) -> list[str]:
    """按物品编码构造使用物品报文（支持 4~20 位且偶数长度 hex，不依赖背包 item_id 字段名）。"""
    code = _normalize_hex_even_range(item_code, 4, 20, "item_code")
    packets = []
    for _ in range(max(1, int(quantity))):
        random_num = random_num_hex6()
        packets.append(
            "1e000000e80308000404" + random_num + "050d0400000c00000000" + code + "0000010000"
        )
    return packets


def build_drop_item_packet(item_id: str, quantity: int = 1) -> tuple[str, int]:
    actual_qty = min(quantity, 100)
    num_hex = hex(actual_qty)[2:].zfill(2)
    random_num = random_num_hex4()
    packet_hex = (
        "1e000000e80308000404"
        + random_num
        + "f5050d0400000c00000001"
        + item_id
        + "0000"
        + num_hex
        + "0000"
    )
    return packet_hex, actual_qty


def build_decompose_packet(item_id: str) -> str:
    random_num = random_num_hex6()
    return "1a000000e8030800412a" + random_num + "05462a000008000000" + item_id + "0000"


def build_exchange_wuling_packet() -> str:
    return "27000000e8030d00fe03f5fff50510040000150000004a02000002069d0900000000000000000001000000"


def build_buy_item_packet(npc_id: str, item_code: str) -> str:
    """
    统一购买构包：
    27000000e8030d00fe03 + random_hex_4 + f5051004000015000000 + npcid(8) + itemcode(14) + 00000000000001000000
    """
    npc = _normalize_hex(npc_id, 8, "npc_id")
    tail = _normalize_hex(item_code, 14, "item_code")
    return (
        "27000000e8030d00fe03"
        + random_num_hex4()
        + "f5051004000015000000"
        + npc
        + tail
        + "00000000000001000000"
    )


def get_current_map_npc_id() -> str:
    """读取会话中的当前地图 NPC id（8 位 hex）。"""
    session = get_session()
    with session._lock:
        npc_id = (session.current_map_npc_id_hex or "").strip().lower()
    if len(npc_id) != 8:
        raise ValueError("当前地图 NPC id 未知，请先进入地图并等待 NPC 列表下行包（e803…4d4f/db07）")
    return _normalize_hex(npc_id, 8, "npc_id")


def pick_decompose_targets(protected_items: list | None = None) -> tuple[list, list]:
    session = get_session()
    if protected_items is None:
        protected_items = []

    with session._lock:
        items = list(session.backpack_items.values())

    to_decompose = []
    skipped = []
    for item in items:
        if not item.can_disassemble:
            continue
        if "黄金" in item.name:
            skipped.append(item.name)
            continue
        if any(kw in item.name for kw in protected_items):
            skipped.append(item.name)
            continue
        to_decompose.append(item)
    return to_decompose, skipped


def optimistic_consume_item(item_id: str, quantity: int) -> tuple[bool, str]:
    return get_session().consume_item(item_id, quantity)


def optimistic_decompose_items(items: list):
    session = get_session()
    with session._lock:
        for item in items:
            existing = session.backpack_items.get(item.item_id)
            if not existing:
                continue
            if existing.quantity <= 1:
                del session.backpack_items[item.item_id]
            else:
                existing.quantity -= 1
    session.notify_backpack_update()
