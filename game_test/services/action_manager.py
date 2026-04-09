"""
统一动作调度层。
负责标准化动作输入、统一发包入口、统一上行记录和错误包装。
"""

from typing import Any, Dict

from core.connector import send_and_receive_once, send_packet
from core.session import get_session
from features import battle, chat, item_use
from features.packet_probe import record_packet


def _ensure_connected() -> Dict[str, Any] | None:
    session = get_session()
    if not session.connected or not session.sock:
        return {"ok": False, "error": "未连接游戏服"}
    return None


def send_raw_action(
    packet_hex: str,
    *,
    direction_tag: str = "UP",
    priority: int = 10,
    use_queue: bool = True,
) -> dict:
    """直接发送原始报文。"""
    session = get_session()
    if not session.sock:
        return {"ok": False, "error": "未连接游戏服"}

    clean_hex = packet_hex.lower().replace(" ", "")
    record_packet(clean_hex, direction_tag)
    try:
        sent_bytes = send_packet(clean_hex, priority=priority, use_queue=use_queue)
        return {
            "ok": True,
            "hex": clean_hex,
            "queued": 1 if use_queue else 0,
            "sent_bytes": sent_bytes,
            "method": "queue" if use_queue else "direct",
        }
    except Exception as e:
        return {"ok": False, "error": f"发包失败: {e}"}


def send_and_wait(packet_hex: str, *, timeout: float, matcher=None) -> dict:
    """发送一次并同步等待一次响应。"""
    session = get_session()
    if not session.sock:
        return {"ok": False, "error": "未连接游戏服"}

    clean_hex = packet_hex.lower().replace(" ", "")
    record_packet(clean_hex, "UP")
    try:
        response = send_and_receive_once(clean_hex, recv_timeout=timeout)
        if not response:
            return {"ok": False, "error": "服务器无响应"}
        record_packet(response, "DN")
        if matcher is not None and not matcher(response):
            return {"ok": False, "error": "响应不符合预期", "response_hex": response.hex()}
        return {"ok": True, "response_bytes": response, "response_hex": response.hex()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def dispatch_feature_action(action_name: str, payload: Dict[str, Any]) -> dict:
    """将动作名映射到具体业务动作。"""
    session = get_session()
    if action_name.startswith("item.") or action_name.startswith("battle.") or action_name == "chat.send":
        err = _ensure_connected()
        if err:
            return err

    if action_name == "item.use":
        item_id = str(payload.get("item_id", "")).strip().lower()
        quantity = int(payload.get("quantity", 1))
        if not item_id:
            return {"ok": False, "error": "item_id 不能为空"}
        if quantity <= 0:
            return {"ok": False, "error": "数量必须大于0"}
        ok, err = item_use.optimistic_consume_item(item_id, quantity)
        if not ok:
            return {"ok": False, "error": err}
        queued = 0
        for packet_hex in item_use.build_use_item_packets(item_id, quantity):
            res = send_raw_action(packet_hex, priority=10, use_queue=True)
            if not res.get("ok"):
                return res
            queued += 1
        session.notify_backpack_update()
        return {"ok": True, "queued": queued}

    if action_name == "item.drop":
        item_id = str(payload.get("item_id", "")).strip().lower()
        quantity = int(payload.get("quantity", 1))
        if not item_id:
            return {"ok": False, "error": "item_id 不能为空"}
        packet_hex, actual_qty = item_use.build_drop_item_packet(item_id, quantity)
        ok, err = item_use.optimistic_consume_item(item_id, actual_qty)
        if not ok:
            return {"ok": False, "error": err}
        res = send_raw_action(packet_hex, priority=1, use_queue=True)
        if not res.get("ok"):
            return res
        session.notify_backpack_update()
        return {"ok": True, "queued": 1, "actual_quantity": actual_qty}

    if action_name == "item.decompose":
        item_id = str(payload.get("item_id", "")).strip().lower()
        if not item_id:
            return {"ok": False, "error": "item_id 不能为空"}
        ok, err = item_use.optimistic_consume_item(item_id, 1)
        if not ok:
            return {"ok": False, "error": err}
        res = send_raw_action(item_use.build_decompose_packet(item_id), priority=0, use_queue=True)
        if not res.get("ok"):
            return res
        session.notify_backpack_update()
        return {"ok": True, "queued": 1}

    if action_name == "item.decompose_all":
        protected_items = payload.get("protected_items", [])
        targets, skipped = item_use.pick_decompose_targets(protected_items)
        queued = []
        for item in targets:
            res = send_raw_action(item_use.build_decompose_packet(item.item_id), priority=0, use_queue=True)
            if res.get("ok"):
                queued.append(item.name)
        item_use.optimistic_decompose_items(targets)
        return {"ok": True, "queued": queued, "skipped": skipped}

    if action_name == "item.exchange_wuling":
        return send_raw_action(item_use.build_exchange_wuling_packet(), priority=10, use_queue=True)

    if action_name == "chat.send":
        message = str(payload.get("message", "")).strip()
        if not message:
            return {"ok": False, "error": "message 不能为空"}
        res = send_raw_action(chat.build_chat_packet(message), priority=10, use_queue=False)
        if not res.get("ok"):
            return res
        return {"ok": True, "sent_bytes": res.get("sent_bytes", 0)}

    if action_name == "battle.start":
        monster_code = str(payload.get("monster_code", "")).strip().lower()
        try:
            built = battle.build_start_battle_packet(monster_code)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        res = send_raw_action(built["packet_hex"], priority=10, use_queue=True)
        if not res.get("ok"):
            return res
        battle.mark_battle_started()
        return {"ok": True, "queued": 1, **built}

    if action_name == "battle.do":
        built = battle.build_do_battle_packet()
        res = send_raw_action(built["packet_hex"], priority=10, use_queue=True)
        if not res.get("ok"):
            return res
        return {"ok": True, "queued": 1, **built}

    if action_name == "battle.one_shot":
        start_res = dispatch_feature_action("battle.start", payload)
        if not start_res.get("ok"):
            return start_res
        fight_res = dispatch_feature_action("battle.do", payload)
        if not fight_res.get("ok"):
            return fight_res
        return {
            "ok": True,
            "queued": 2,
            "monster_code": start_res.get("monster_code"),
            "random_num": start_res.get("random_num"),
        }

    return {"ok": False, "error": f"未知动作: {action_name}"}


def send_action(action_name: str, payload: Dict[str, Any], *, priority: int = 10, use_queue: bool = True) -> dict:
    """统一动作入口。priority/use_queue 当前主要用于原始动作保留扩展位。"""
    if action_name == "probe.send":
        packet_hex = str(payload.get("hex", "")).strip()
        if not packet_hex:
            return {"ok": False, "error": "hex 不能为空"}
        return send_raw_action(packet_hex, priority=priority, use_queue=use_queue)
    return dispatch_feature_action(action_name, payload)
