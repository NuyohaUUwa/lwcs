"""
背包报文解析（实时背包数量更新）：
- e8030100d607：权威全量背包列表，收到后整体覆盖内存中的背包。
- ec07 / ed07 / e607：非 d607 场景只做“数量增减”（不覆盖整个数量）。

其中“增/减”的方向尽量根据报文文本（`得到/获得/失去/丢失` 等关键词）做推断，
作为乐观更新的 delta_sign 输入；若文本不可用，则退化为增（+1）。
"""

import binascii

from game_test.core.codec import extract_utf8_segments, find_all_positions, slice_game_frame_hex_at
from game_test.backend.runtime import Item, get_session
from game_test.backend.domain.roles.role_stats import merge_role_stats_from_packet, update_session_stats

# 权威背包列表指纹（与下行 hex 偏移 [8:20] 对齐，12 hex 字符）
_AUTH_BACKPACK_LIST_FP = "e8030100d607"

_GAIN_KEYWORDS = ("得到", "获得")
_LOSE_KEYWORDS = ("失去", "丢失")
_S1_GIFT_KEYWORDS = ("s1", "礼包")
_JOB_PROTECTED_EQUIPMENT = {
    "侠客": ("侠士战甲", "侠士头盔"),
    "刺客": ("刺客战甲", "刺客头盔"),
    "术士": ("术士战甲", "术士头盔"),
}


def _infer_delta_sign_from_text(utf8_text: str | None) -> int | None:
    """根据文本关键词推断背包数量的增/减方向。"""
    if not utf8_text:
        return None
    # 统一压缩文本，避免换行/特殊标记影响 contains 判断
    compact = str(utf8_text).replace("\r", "").replace("\n", "").strip()
    if any(k in compact for k in _LOSE_KEYWORDS):
        return -1
    if any(k in compact for k in _GAIN_KEYWORDS):
        return 1
    return None


def _apply_backpack_items_delta(items: list[Item], *, delta_sign: int) -> None:
    """
    统一封装：非 d607 场景根据 delta_sign 对背包中同 item_id 做数量增减。
    """
    session = get_session()
    if session.apply_optimistic_obtain_items(items, delta_sign=delta_sign):
        session.notify_backpack_update()


def is_s1_gift_use_text(text: str | None) -> bool:
    compact = str(text or "").replace("\r", "").replace("\n", "").strip().lower()
    used_idx = compact.find("已使用")
    if used_idx >= 0:
        compact = compact[used_idx + len("已使用") :]
        stops = [idx for marker in ("获得", "得到") if (idx := compact.find(marker)) >= 0]
        if stops:
            compact = compact[: min(stops)]
    return all(k in compact for k in _S1_GIFT_KEYWORDS)


def should_auto_decompose_item(item: Item, role_job: str | None) -> bool:
    if not item.can_disassemble:
        return False
    name = str(item.name or "")
    if "黄金" in name:
        return False
    protected = _JOB_PROTECTED_EQUIPMENT.get(str(role_job or ""), ())
    return not any(protected_name in name for protected_name in protected)


def _auto_decompose_candidates(items: list[Item], role_job: str | None) -> list[Item]:
    by_id: dict[str, Item] = {}
    for item in items:
        if should_auto_decompose_item(item, role_job):
            by_id[item.item_id] = item
    return list(by_id.values())


def _extract_embedded_obtained_items(packet_hex: str) -> list[Item]:
    packet_hex_l = packet_hex.lower()
    marker = "e8030100ed07"
    start = 0
    out: list[Item] = []
    while True:
        idx = packet_hex_l.find(marker, start)
        if idx < 8:
            break
        frame_start = idx - 8
        sub_packet = slice_game_frame_hex_at(packet_hex, frame_start)
        if sub_packet is not None:
            out.extend(_collect_items(sub_packet, "cd00"))
        start = idx + len(marker)
    return out


def _maybe_auto_decompose_s1_gift_items(
    packet_hex: str,
    *,
    utf8_text: str | None,
    root_items: list[Item],
    delta_sign: int,
) -> None:
    if delta_sign <= 0:
        return
    session = get_session()
    if not session.auto_decompose_s1_enabled:
        return
    trigger_text = utf8_text or extract_utf8_segments(packet_hex)
    if not is_s1_gift_use_text(trigger_text):
        return
    embedded_items = _extract_embedded_obtained_items(packet_hex)
    if not root_items and not embedded_items:
        with session._lock:
            session.auto_decompose_s1_pending = True
        return
    role_job = session.current_role.role_job if session.current_role else ""
    candidates = _auto_decompose_candidates(
        list(root_items) + embedded_items,
        role_job,
    )
    _send_auto_decompose_actions(candidates)


def _consume_pending_auto_decompose_s1_items(items: list[Item]) -> None:
    session = get_session()
    with session._lock:
        pending = bool(session.auto_decompose_s1_pending)
        if pending:
            session.auto_decompose_s1_pending = False
    if not pending:
        return
    role_job = session.current_role.role_job if session.current_role else ""
    _send_auto_decompose_actions(_auto_decompose_candidates(items, role_job))


def _send_auto_decompose_actions(items: list[Item]) -> None:
    if not items:
        return
    from game_test.backend.runtime.actions import send_action

    for item in items:
        res = send_action("item.decompose", {"item_id": item.item_id})
        if not res.get("ok"):
            print(f"[backpack] 自动分解失败: {item.name}({item.item_id}) - {res.get('error', '未知错误')}")


def dispatch_backpack_packet(packet_hex: str, utf8_text: str | None = None) -> bool:
    """
    尝试对下行报文进行背包相关解析。
    返回 True 表示已处理，False 表示不是背包报文。
    """
    fingerprint = packet_hex[8:20].lower() if len(packet_hex) >= 20 else ""

    if fingerprint == _AUTH_BACKPACK_LIST_FP:
        _parse_backpack_list_authoritative(packet_hex)
        return True

    # 非 d607：通过文本推断 delta_sign；不可推断则退化为 +1（获得类）。
    inferred_sign = _infer_delta_sign_from_text(utf8_text)
    delta_sign = inferred_sign if inferred_sign is not None else 1

    if "e607" in fingerprint:
        _parse_item_bought(packet_hex, delta_sign=delta_sign)
        return True
    if "ec07" in fingerprint:
        _parse_backpack_change(packet_hex, delta_sign=delta_sign, utf8_text=utf8_text)
        return True
    if "ed07" in fingerprint:
        _parse_item_obtained(packet_hex, delta_sign=delta_sign)
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
            # 数量：item_id（6 字节）后固定再 2 字节 padding，接着 2 字节 uint16 LE（与 d607 实际报文对齐）。
            # 旧实现取 [pos+17:pos+22] 共 5 个 hex 半字节错位，会把 0x0140(320) 读成 0x40(64)、把 0x0773(1907) 读成 0x73(115)。
            qty_hex = packet_hex[pos + 20 : pos + 24]
            if len(qty_hex) < 4:
                continue
            item_num = int.from_bytes(bytes.fromhex(qty_hex), "little")

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
#  乐观增减更新                                                         #
# ------------------------------------------------------------------ #


def _parse_backpack_change(packet_hex: str, *, delta_sign: int, utf8_text: str | None = None):
    """ec07：非 d607，仅做数量增减；内嵌 ed07 子包同样按 delta_sign 执行。"""
    items = _items_for_obtain_and_bought(packet_hex)
    session = get_session()
    changed = session.apply_optimistic_obtain_items(items, delta_sign=delta_sign)
    if "e88eb7e5be97efbc9a" in packet_hex.lower():
        changed |= _parse_embedded_obtained_packets(packet_hex, delta_sign=delta_sign)
    if changed:
        session.notify_backpack_update()
    _maybe_auto_decompose_s1_gift_items(
        packet_hex,
        utf8_text=utf8_text,
        root_items=[it for it in items if it.can_disassemble],
        delta_sign=delta_sign,
    )


def _parse_item_obtained(packet_hex: str, *, delta_sign: int):
    """ed07：非 d607，仅做数量增减（根据文本推断 delta_sign）。"""
    items = _items_for_obtain_and_bought(packet_hex)
    _apply_backpack_items_delta(items, delta_sign=delta_sign)
    if delta_sign > 0:
        _consume_pending_auto_decompose_s1_items(items)


def _parse_item_bought(packet_hex: str, *, delta_sign: int):
    """e607：与获得类一致，非 d607 仅做数量增减（根据文本推断 delta_sign）。"""
    _apply_backpack_items_delta(_items_for_obtain_and_bought(packet_hex), delta_sign=delta_sign)


def _parse_embedded_obtained_packets(packet_hex: str, *, delta_sign: int) -> bool:
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
        if session.apply_optimistic_obtain_items(_items_for_obtain_and_bought(sub_packet), delta_sign=delta_sign):
            changed = True
        with session._lock:
            full_next = session.role_stats_full_refresh_on_next_ed07
        if full_next:
            if not update_session_stats(sub_packet):
                merge_role_stats_from_packet(sub_packet)
            with session._lock:
                session.role_stats_full_refresh_on_next_ed07 = False
        else:
            merge_role_stats_from_packet(sub_packet)
        start = idx + len(marker)
    return changed


# ------------------------------------------------------------------ #
#  对外辅助                                                             #
# ------------------------------------------------------------------ #


def get_backpack_snapshot() -> list[dict[str, object]]:
    """返回当前背包快照列表（dict 列表）。"""
    return get_session().get_backpack_list()
