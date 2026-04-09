"""
物品业务协议。
负责构造业务报文与执行乐观状态更新，不直接发包。
"""

from core.session import get_session
from utils.random_num import random_num_hex4, random_num_hex6


def build_use_item_packets(item_id: str, quantity: int = 1) -> list[str]:
    packets = []
    for _ in range(quantity):
        random_num = random_num_hex6()
        packets.append(
            "1e000000e80308000404" + random_num + "050d0400000c00000000" + item_id + "0000010000"
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
