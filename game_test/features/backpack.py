"""
背包报文解析：
- e8030100d607：权威全量背包列表，收到后整体替换内存中的背包。
- ec07 / ed07 / e607：在 d607 基线上的乐观更新；数量>0 时可 upsert（含从零新增），
  数量<=0 时不新增，若已有该物品则移除。
"""

import binascii

from core.codec import find_all_positions, slice_game_frame_hex_at
from core.session import get_session, Item
from features.role_stats import merge_role_stats_from_packet

# 权威背包列表指纹（与下行 hex 偏移 [8:20] 对齐，12 hex 字符）
_AUTH_BACKPACK_LIST_FP = "e8030100d607"


def dispatch_backpack_packet(packet_hex: str) -> bool:
    """
    尝试对下行报文进行背包相关解析。
    返回 True 表示已处理，False 表示不是背包报文。
    """
    fingerprint = packet_hex[8:20].lower() if len(packet_hex) >= 20 else ""

    if fingerprint == _AUTH_BACKPACK_LIST_FP:
        _parse_backpack_list_authoritative(packet_hex)
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
#  解析：从报文提取 Item 列表（不写 session）                              #
# ------------------------------------------------------------------ #


def _collect_items(packet_hex: str, item_type: str) -> list[Item]:
    """
    在 packet_hex 中查找所有 item_type 标记（cd00 可分解 / ce00 不可分解），
    按偏移提取 item_id、数量、中文名。
    """
    can_disassemble = item_type == "cd00"
    positions = find_all_positions(packet_hex, item_type)
    out: list[Item] = []
    for pos in positions:
        try:
            item_id = packet_hex[pos + 4 : pos + 16]
            item_num_hex = packet_hex[pos + 17 : pos + 22]
            item_num = int(item_num_hex, 16)

            name_len_hex = packet_hex[pos + 32 : pos + 38]
            name_byte_len = int(name_len_hex, 16)
            name_hex = packet_hex[pos + 40 : pos + 40 + name_byte_len * 2]
            name_cn = binascii.unhexlify(name_hex).decode("utf-8")

            out.append(
                Item(
                    item_id=item_id,
                    name=name_cn,
                    quantity=item_num,
                    can_disassemble=can_disassemble,
                )
            )
        except Exception as e:
            print(f"[backpack] 解析物品失败 (type={item_type}, pos={pos}): {e}")
    return out


def _items_for_obtain_and_bought(packet_hex: str) -> list[Item]:
    return _collect_items(packet_hex, "ce00") + _collect_items(packet_hex, "cd00")


# ------------------------------------------------------------------ #
#  d607 权威全量                                                         #
# ------------------------------------------------------------------ #


def _parse_backpack_list_authoritative(packet_hex: str):
    """e8030100d607：全量替换背包；仅保留解析成功且数量>0 的条目。"""
    session = get_session()
    by_id: dict[str, Item] = {}
    for it in _collect_items(packet_hex, "ce00"):
        if it.quantity > 0:
            by_id[it.item_id] = it
    for it in _collect_items(packet_hex, "cd00"):
        if it.quantity > 0:
            by_id[it.item_id] = it
    session.replace_backpack_items(by_id)
    session.notify_backpack_update()


# ------------------------------------------------------------------ #
#  乐观更新                                                              #
# ------------------------------------------------------------------ #


def _parse_backpack_change(packet_hex: str):
    """ec07：体部与 ed07/e607 相同规则（可从零新增）；内嵌 ed07 子包同样 upsert。"""
    session = get_session()
    changed = session.apply_optimistic_obtain_items(_items_for_obtain_and_bought(packet_hex))
    if "e88eb7e5be97efbc9a" in packet_hex.lower():
        changed |= _parse_embedded_obtained_packets(packet_hex)
    if changed:
        session.notify_backpack_update()


def _parse_item_obtained(packet_hex: str):
    """ed07：可新增物品；数量<=0 不新增，已有则移除。"""
    session = get_session()
    if session.apply_optimistic_obtain_items(_items_for_obtain_and_bought(packet_hex)):
        session.notify_backpack_update()


def _parse_item_bought(packet_hex: str):
    """e607：与获得类一致，乐观 upsert。"""
    session = get_session()
    if session.apply_optimistic_obtain_items(_items_for_obtain_and_bought(packet_hex)):
        session.notify_backpack_update()


def _parse_embedded_obtained_packets(packet_hex: str) -> bool:
    """
    ec07 中拼接的 ed07 子包（获得）；不单独 notify，由调用方统一广播。
    """
    session = get_session()
    packet_hex_l = packet_hex.lower()
    marker = "e8030100ed07"
    start = 0
    changed = False
    while True:
        idx = packet_hex_l.find(marker, start)
        if idx < 8:
            break
        frame_start = idx - 8
        sub_packet = slice_game_frame_hex_at(packet_hex, frame_start)
        if sub_packet is None:
            start = idx + len(marker)
            continue
        if session.apply_optimistic_obtain_items(_items_for_obtain_and_bought(sub_packet)):
            changed = True
        merge_role_stats_from_packet(sub_packet)
        start = idx + len(marker)
    return changed


# ------------------------------------------------------------------ #
#  对外辅助                                                             #
# ------------------------------------------------------------------ #


def get_backpack_snapshot() -> list:
    """返回当前背包快照列表（dict 列表）。"""
    return get_session().get_backpack_list()
