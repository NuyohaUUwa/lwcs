"""
物品操作：使用、丢弃、分解、兑换五灵。
所有操作均通过 GameSession 发送队列异步发包，不阻塞等待服务器响应。
"""

import random

from core.connector import enqueue_packet
from core.session import get_session
from features.packet_probe import record_packet


def use_item(item_id: str, quantity: int = 1) -> dict:
    """
    使用物品（循环发包 quantity 次）。
    发包前校验背包库存，发包后乐观扣减数量并广播背包更新。

    模板：1e000000e80308000404 + random_6hex + 050d0400000c00000000 + item_id + 0000010000
    """
    session = get_session()
    if not session.connected:
        return {"ok": False, "error": "未连接游戏服"}
    if quantity <= 0:
        return {"ok": False, "error": "数量必须大于0"}

    # 校验并乐观扣减（原子操作，防止数量为负或物品不存在）
    ok, err = session.consume_item(item_id, quantity)
    if not ok:
        return {"ok": False, "error": err}

    sent = 0
    for _ in range(quantity):
        random_num = hex(random.randint(0x100000, 0x1000000))[2:].zfill(6)
        packet_hex = (
            "1e000000e80308000404"
            + random_num
            + "050d0400000c00000000"
            + item_id
            + "0000010000"
        )
        record_packet(packet_hex, "UP")
        enqueue_packet(session.send_queue, packet_hex, priority=10)
        sent += 1

    # 广播乐观更新后的背包给前端（服务器后续推送真实数据时再次覆盖）
    session.notify_backpack_update()
    return {"ok": True, "queued": sent}


def drop_item(item_id: str, quantity: int = 1) -> dict:
    """
    丢弃物品。
    quantity > 100 时按 100 处理（服务器限制）。
    发包前校验库存，发包后乐观扣减并广播更新。

    模板：1e000000e80308000404 + random_4hex + f5050d0400000c00000001 + item_id + 0000 + num_hex + 0000
    """
    session = get_session()
    if not session.connected:
        return {"ok": False, "error": "未连接游戏服"}

    actual_qty = min(quantity, 100)

    # 校验并乐观扣减
    ok, err = session.consume_item(item_id, actual_qty)
    if not ok:
        return {"ok": False, "error": err}

    num_hex = hex(actual_qty)[2:].zfill(2)
    random_num = hex(random.randint(0x0000, 0xFFFF))[2:].zfill(4)
    packet_hex = (
        "1e000000e80308000404"
        + random_num
        + "f5050d0400000c00000001"
        + item_id
        + "0000"
        + num_hex
        + "0000"
    )
    record_packet(packet_hex, "UP")
    enqueue_packet(session.send_queue, packet_hex, priority=1)
    session.notify_backpack_update()
    return {"ok": True, "queued": 1, "actual_quantity": actual_qty}


def decompose_item(item_id: str) -> dict:
    """
    分解装备（消耗 1 个）。
    发包前校验物品存在，发包后乐观扣减并广播更新。

    模板：1a000000e8030800412a + random_6hex + 05462a000008000000 + item_id + 0000
    """
    session = get_session()
    if not session.connected:
        return {"ok": False, "error": "未连接游戏服"}

    # 校验并乐观扣减 1 个
    ok, err = session.consume_item(item_id, 1)
    if not ok:
        return {"ok": False, "error": err}

    random_num = hex(random.randint(0x100000, 0x1000000))[2:].zfill(6)
    packet_hex = (
        "1a000000e8030800412a"
        + random_num
        + "05462a000008000000"
        + item_id
        + "0000"
    )
    record_packet(packet_hex, "UP")
    enqueue_packet(session.send_queue, packet_hex, priority=0)
    session.notify_backpack_update()
    return {"ok": True, "queued": 1}


def exchange_wuling() -> dict:
    """
    兑换五灵（固定报文）。
    """
    session = get_session()
    if not session.connected:
        return {"ok": False, "error": "未连接游戏服"}

    packet_hex = "27000000e8030d00fe03f5fff50510040000150000004a02000002069d0900000000000000000001000000"
    record_packet(packet_hex, "UP")
    enqueue_packet(session.send_queue, packet_hex, priority=10)
    return {"ok": True, "queued": 1}


def one_key_decompose(protected_items: list = None) -> dict:
    """
    一键分解背包中所有可分解装备（排除保护列表中的物品名称）。

    Args:
        protected_items: 不允许分解的物品名称关键字列表，如 ["侠士战甲", "侠士头盔"]
    """
    session = get_session()
    if not session.connected:
        return {"ok": False, "error": "未连接游戏服"}

    if protected_items is None:
        protected_items = []

    queued = []
    skipped = []
    with session._lock:
        items = list(session.backpack_items.values())

    # 筛选可分解物品
    to_decompose = []
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

    if not to_decompose:
        return {"ok": True, "queued": queued, "skipped": skipped}

    # 发包
    for item in to_decompose:
        random_num = hex(random.randint(0x100000, 0x1000000))[2:].zfill(6)
        packet_hex = (
            "1a000000e8030800412a"
            + random_num
            + "05462a000008000000"
            + item.item_id
            + "0000"
        )
        record_packet(packet_hex, "UP")
        enqueue_packet(session.send_queue, packet_hex, priority=0)
        queued.append(item.name)

    # 乐观扣减：从背包移除已分解物品，广播一次更新
    with session._lock:
        for item in to_decompose:
            existing = session.backpack_items.get(item.item_id)
            if existing:
                if existing.quantity <= 1:
                    del session.backpack_items[item.item_id]
                else:
                    existing.quantity -= 1
    session.notify_backpack_update()

    return {"ok": True, "queued": queued, "skipped": skipped}
