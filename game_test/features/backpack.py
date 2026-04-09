"""
背包报文解析：
处理下行指纹 d607 / ec07 / ed07 三类报文，
更新 GameSession.backpack_items，并广播背包变更事件。
"""

import binascii
from typing import Optional

from core.codec import find_all_positions
from core.session import get_session, Item


# 背包物品指纹类型映射
_FINGERPRINT_MAP = {
    "e8030100d607": "backpack_list",       # 背包物品列表（S1礼包等）
    "e8030100e607": "item_bought",         # 购买成功
    "e8030100ec07": "backpack_used",       # 背包已使用/礼包
    "e8030100ed07": "item_obtained",       # 获得物品
}


def dispatch_backpack_packet(packet_hex: str) -> bool:
    """
    尝试对下行报文进行背包相关解析。
    返回 True 表示已处理，False 表示不是背包报文。
    """
    fingerprint = packet_hex[8:20] if len(packet_hex) >= 20 else ""

    if "d607" in fingerprint:
        _parse_backpack_list(packet_hex)
        return True
    if "e607" in fingerprint:
        _parse_item_bought(packet_hex)
        return True
    if "ec07" in fingerprint:
        _parse_backpack_change(packet_hex)
        return True
    if "ed07" in fingerprint:
        _parse_item_obtained(packet_hex)
        return True
    return False


# ------------------------------------------------------------------ #
#  内部解析函数                                                         #
# ------------------------------------------------------------------ #

def _parse_items_by_type(packet_hex: str, item_type: str):
    """
    通用物品解析：在 packet_hex 中查找所有 item_type 标记位置，
    按偏移规则提取 item_id / 数量 / 中文名，更新 session。
    item_type: "cd00"（可分解）或 "ce00"（不可分解）
    """
    session = get_session()
    can_disassemble = (item_type == "cd00")
    positions = find_all_positions(packet_hex, item_type)

    changed = False
    for pos in positions:
        try:
            item_id = packet_hex[pos + 4:pos + 16]
            item_num_hex = packet_hex[pos + 17:pos + 22]
            item_num = int(item_num_hex, 16)

            name_len_hex = packet_hex[pos + 32:pos + 38]
            name_byte_len = int(name_len_hex, 16)
            name_hex = packet_hex[pos + 40:pos + 40 + name_byte_len * 2]
            name_cn = binascii.unhexlify(name_hex).decode('utf-8')

            item = Item(
                item_id=item_id,
                name=name_cn,
                quantity=item_num,
                can_disassemble=can_disassemble,
            )
            session.update_item(item)
            changed = True
        except Exception as e:
            print(f"[backpack] 解析物品失败 (type={item_type}, pos={pos}): {e}")

    if changed:
        session.notify_backpack_update()


def _parse_backpack_list(packet_hex: str):
    """处理 d607 背包列表报文（含可分解 cd00 和不可分解 ce00 标记）。"""
    _parse_items_by_type(packet_hex, "ce00")
    _parse_items_by_type(packet_hex, "cd00")


def _parse_backpack_change(packet_hex: str):
    """处理 ec07 背包变化报文（礼包使用、消耗等）。"""
    _parse_items_by_type(packet_hex, "ce00")
    _parse_items_by_type(packet_hex, "cd00")
    if "e88eb7e5be97efbc9a" in packet_hex.lower():
        _parse_embedded_obtained_packets(packet_hex)


def _parse_item_obtained(packet_hex: str):
    """处理 ed07 获得物品报文。"""
    _parse_items_by_type(packet_hex, "ce00")
    _parse_items_by_type(packet_hex, "cd00")


def _parse_item_bought(packet_hex: str):
    """处理 e607 购买成功报文。"""
    _parse_items_by_type(packet_hex, "ce00")
    _parse_items_by_type(packet_hex, "cd00")


def _parse_embedded_obtained_packets(packet_hex: str):
    """
    处理 ec07 中拼接的 ed07 子包。
    常见场景：'已使用xxx礼包，获得：' 文本后直接跟一段 ed07 物品获得报文。
    """
    packet_hex_l = packet_hex.lower()
    marker = "e8030100ed07"
    start = 0
    while True:
        idx = packet_hex_l.find(marker, start)
        if idx < 8:
            break
        sub_packet = packet_hex[idx - 8:]
        _parse_item_obtained(sub_packet)
        start = idx + len(marker)


# ------------------------------------------------------------------ #
#  对外辅助                                                             #
# ------------------------------------------------------------------ #

def get_backpack_snapshot() -> list:
    """返回当前背包快照列表（dict 列表）。"""
    return get_session().get_backpack_list()
