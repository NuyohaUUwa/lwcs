"""
统一动作调度层。
负责标准化动作输入、统一发包入口、统一上行记录和错误包装。
"""

from typing import Any, Dict
import binascii

from config import DEFAULT_BATTLE_LOOP_DELAY_MS
from core.connector import send_and_receive_once, send_packet
from core.session import get_session
from features import battle, chat, item_use, teleport


def _ensure_connected() -> Dict[str, Any] | None:
    session = get_session()
    if not session.connected or not session.sock:
        return {"ok": False, "error": "未连接游戏服"}
    return None


def _validate_packet_hex(packet_hex: str) -> tuple[bool, str, str, str]:
    clean_hex = str(packet_hex or "").lower().replace(" ", "").replace("\n", "").replace("\r", "").replace("\t", "")
    if not clean_hex:
        return False, "", "hex 不能为空", ""
    if len(clean_hex) % 2 != 0:
        return False, "", "hex 长度必须为偶数", ""
    try:
        raw = binascii.unhexlify(clean_hex)
    except binascii.Error as e:
        return False, "", f"hex 格式错误: {e}", ""
    if len(raw) < 4:
        return True, clean_hex, "", f"报文长度不足，至少需要 4 字节长度头: {clean_hex}"
    header_len = int.from_bytes(raw[:4], "little") + 4
    actual_len = len(raw)
    if header_len != actual_len:
        return True, clean_hex, "", f"报文长度不匹配: 长度字段={header_len}，实际长度={actual_len}，报文={clean_hex}"
    return True, clean_hex, "", ""


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

    ok, clean_hex, err, warn = _validate_packet_hex(packet_hex)
    if not ok:
        return {"ok": False, "error": err}
    if warn:
        print(f"[action_manager] {warn}")
    try:
        sent_bytes = send_packet(clean_hex, priority=priority, use_queue=use_queue)
        result = {
            "ok": True,
            "hex": clean_hex,
            "queued": 1 if use_queue else 0,
            "sent_bytes": sent_bytes,
            "method": "queue" if use_queue else "direct",
        }
        if warn:
            result["validation_warning"] = warn
        return result
    except Exception as e:
        return {"ok": False, "error": f"发包失败: {e}"}


def send_and_wait(packet_hex: str, *, timeout: float, matcher=None) -> dict:
    """发送一次并同步等待一次响应。"""
    session = get_session()
    if not session.sock:
        return {"ok": False, "error": "未连接游戏服"}

    ok, clean_hex, err, warn = _validate_packet_hex(packet_hex)
    if not ok:
        return {"ok": False, "error": err}
    if warn:
        print(f"[action_manager] {warn}")
    try:
        response = send_and_receive_once(clean_hex, recv_timeout=timeout)
        if not response:
            return {"ok": False, "error": "服务器无响应"}
        if matcher is not None and not matcher(response):
            return {"ok": False, "error": "响应不符合预期", "response_hex": response.hex()}
        result = {"ok": True, "response_bytes": response, "response_hex": response.hex()}
        if warn:
            result["validation_warning"] = warn
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


def dispatch_feature_action(action_name: str, payload: Dict[str, Any]) -> dict:
    """将动作名映射到具体业务动作。"""
    session = get_session()
    if (
        action_name.startswith("item.")
        or action_name.startswith("battle.")
        or action_name == "chat.send"
        or action_name == "teleport.go"
    ):
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
        warnings = []
        for packet_hex in item_use.build_use_item_packets(item_id, quantity):
            res = send_raw_action(packet_hex, priority=10, use_queue=True)
            if not res.get("ok"):
                return res
            queued += 1
            if res.get("validation_warning"):
                warnings.append(res["validation_warning"])
        session.notify_backpack_update()
        result = {"ok": True, "queued": queued}
        if warnings:
            result["validation_warning"] = " | ".join(dict.fromkeys(warnings))
        return result

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
        result = {"ok": True, "queued": 1, "actual_quantity": actual_qty}
        if res.get("validation_warning"):
            result["validation_warning"] = res["validation_warning"]
        return result

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
        result = {"ok": True, "queued": 1}
        if res.get("validation_warning"):
            result["validation_warning"] = res["validation_warning"]
        return result

    if action_name == "item.decompose_all":
        protected_items = payload.get("protected_items", [])
        targets, skipped = item_use.pick_decompose_targets(protected_items)
        queued = []
        warnings = []
        for item in targets:
            res = send_raw_action(item_use.build_decompose_packet(item.item_id), priority=0, use_queue=True)
            if res.get("ok"):
                queued.append(item.name)
                if res.get("validation_warning"):
                    warnings.append(res["validation_warning"])
        item_use.optimistic_decompose_items(targets)
        result = {"ok": True, "queued": queued, "skipped": skipped}
        if warnings:
            result["validation_warning"] = " | ".join(dict.fromkeys(warnings))
        return result

    if action_name == "item.exchange_wuling":
        return send_raw_action(item_use.build_exchange_wuling_packet(), priority=10, use_queue=True)

    if action_name == "item.buy":
        npc_id = str(payload.get("npc_id", "")).strip().lower()
        item_code = str(payload.get("item_code", "")).strip().lower()
        try:
            if not npc_id:
                npc_id = item_use.get_current_map_npc_id()
            packet_hex = item_use.build_buy_item_packet(npc_id, item_code)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        res = send_raw_action(packet_hex, priority=10, use_queue=True)
        if not res.get("ok"):
            return res
        result = {"ok": True, "queued": 1, "npc_id": npc_id, "item_code": item_code}
        if res.get("validation_warning"):
            result["validation_warning"] = res["validation_warning"]
        return result

    if action_name == "chat.send":
        message = str(payload.get("message", "")).strip()
        if not message:
            return {"ok": False, "error": "message 不能为空"}
        res = send_raw_action(chat.build_chat_packet(message), priority=10, use_queue=False)
        if not res.get("ok"):
            return res
        result = {"ok": True, "sent_bytes": res.get("sent_bytes", 0)}
        if res.get("validation_warning"):
            result["validation_warning"] = res["validation_warning"]
        return result

    if action_name == "battle.start":
        monster_code = str(payload.get("monster_code", "")).strip().lower()
        run_pre_battle_actions = bool(payload.get("run_pre_battle_actions", False))
        return battle.start_single_battle(monster_code, run_pre_battle_actions=run_pre_battle_actions)

    if action_name == "battle.loop.start":
        monster_code = str(payload.get("monster_code", "")).strip().lower()
        loop_delay_ms = int(payload.get("loop_delay_ms", DEFAULT_BATTLE_LOOP_DELAY_MS))
        return battle.start_battle_loop(monster_code, loop_delay_ms=loop_delay_ms)

    if action_name == "battle.loop.stop":
        return battle.stop_battle_loop(str(payload.get("reason", "")).strip())

    if action_name == "battle.do":
        ok, err = battle.can_manual_battle_do()
        if not ok:
            return {"ok": False, "error": err, "battle_state": battle.get_battle_state_snapshot()}
        built = battle.build_do_battle_packet()
        res = send_raw_action(built["packet_hex"], priority=10, use_queue=True)
        if not res.get("ok"):
            return res
        battle._set_battle_state(
            state=battle.BATTLE_STATE_WAITING_ACTION_RESULT,
            in_progress=True,
            last_action="f703",
            can_create_next=False,
            last_result={"source": "manual_api", "sent": "f703", "random_num": built["random_num"]},
        )
        result = {"ok": True, "queued": 1, **built, "battle_state": battle.get_battle_state_snapshot()}
        if res.get("validation_warning"):
            result["validation_warning"] = res["validation_warning"]
        return result

    if action_name == "battle.one_shot":
        return battle.start_single_battle(
            str(payload.get("monster_code", "")).strip().lower(),
            run_pre_battle_actions=bool(payload.get("run_pre_battle_actions", False)),
        )

    if action_name == "teleport.go":
        destination = str(payload.get("destination", "")).strip()
        try:
            built = teleport.build_teleport_packet(destination)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        res = send_raw_action(built["packet_hex"], priority=10, use_queue=True)
        if not res.get("ok"):
            return res
        result = {"ok": True, "queued": 1, **built}
        if res.get("validation_warning"):
            result["validation_warning"] = res["validation_warning"]
        return result

    return {"ok": False, "error": f"未知动作: {action_name}"}


def send_action(action_name: str, payload: Dict[str, Any], *, priority: int = 10, use_queue: bool = True) -> dict:
    """统一动作入口。priority/use_queue 当前主要用于原始动作保留扩展位。"""
    if action_name == "probe.send":
        packet_hex = str(payload.get("hex", "")).strip()
        if not packet_hex:
            return {"ok": False, "error": "hex 不能为空"}
        return send_raw_action(packet_hex, priority=priority, use_queue=use_queue)
    return dispatch_feature_action(action_name, payload)
