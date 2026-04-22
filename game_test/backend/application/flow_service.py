"""HTTP-facing complex flow façade (star-stone, transport-supply, liaoguo)."""

import threading
import time
from typing import Any

from game_test.backend.runtime import get_session
from game_test.backend.runtime.actions import send_action


def _emit_flow_log(scope: str, message: str, *, level: str = "info", **extra):
    session = get_session()
    payload = {"scope": scope, "level": level, "message": message}
    if extra:
        payload.update(extra)
    session._notify_sse("control_log", payload)


def _emit_flow_status() -> None:
    session = get_session()
    with _star_stone_state.lock:
        ss_running = _star_stone_state.running
    with _transport_supply_state.lock:
        ts_running = _transport_supply_state.running
    with _liaoguo_state.lock:
        lg_running = _liaoguo_state.running
    session._notify_sse("flow_status", {
        "star_stone_running": ss_running,
        "transport_supply_running": ts_running,
        "liaoguo_running": lg_running,
    })


# =============================================================================
# Star Stone Loop
# =============================================================================
_STAR_STONE_TARGET_NAME_RE = "特步"
_STAR_STONE_LOOP_INTERVAL_S = 1.0
_STAR_STONE_PHASE_IDLE = "idle"
_STAR_STONE_PHASE_BUY = "buy"
_STAR_STONE_PHASE_DECOMPOSE = "decompose"


class _StarStoneState:
    def __init__(self):
        self.running = False
        self.phase = _STAR_STONE_PHASE_IDLE
        self.loop_count = 0
        self.total_stone = 0
        self.total_fragment = 0
        self.stop_event = threading.Event()
        self.lock = threading.Lock()


_star_stone_state = _StarStoneState()
_star_stone_thread: threading.Thread | None = None


def get_star_stone_status() -> dict[str, Any]:
    with _star_stone_state.lock:
        return {
            "ok": True,
            "running": _star_stone_state.running,
            "phase": _star_stone_state.phase,
            "loop_count": _star_stone_state.loop_count,
            "total_stone": _star_stone_state.total_stone,
            "total_fragment": _star_stone_state.total_fragment,
        }


def _decode_packet_text(raw_hex: str) -> str:
    try:
        clean = raw_hex.replace(" ", "").lower()
        raw_bytes = bytes.fromhex(clean[16:] if len(clean) > 16 else clean)
        return raw_bytes.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _find_star_stone_buy_favorite() -> dict[str, Any] | None:
    from game_test.backend.infrastructure.data_manager import load_buy_items

    items = load_buy_items()
    for x in items:
        if _STAR_STONE_TARGET_NAME_RE in (x.get("name") or ""):
            return x
    return None


def _run_star_stone_loop() -> None:
    global _star_stone_thread
    while True:
        if _star_stone_state.stop_event.wait(timeout=_STAR_STONE_LOOP_INTERVAL_S):
            _emit_flow_status()
            return

        favorite = _find_star_stone_buy_favorite()
        if not favorite:
            _emit_flow_log("star_stone", "未找到常用购买物品「特步鞋」", level="err")
            _stop_star_stone_internal()
            return

        session = get_session()
        npc_id = ""
        with session._lock:
            npc_id = session.current_map_npc_id_hex
        if not npc_id or len(npc_id) != 8:
            _emit_flow_log("star_stone", "当前地图 NPC id 未知，请先触发地图 NPC 列表", level="err")
            _stop_star_stone_internal()
            return

        with _star_stone_state.lock:
            _star_stone_state.phase = _STAR_STONE_PHASE_BUY

        res = send_action("item.buy", {"npc_id": npc_id, "item_code": favorite.get("code", "")})
        if not res.get("ok"):
            _emit_flow_log("star_stone", f"购买失败: {res.get('error', '未知错误')}", level="err")
            _stop_star_stone_internal()
            return

        _emit_flow_log(
            "star_stone",
            f"第{_star_stone_state.loop_count + 1}轮：已发送购买 {favorite.get('name', '')}",
        )

        with _star_stone_state.lock:
            _star_stone_state.phase = _STAR_STONE_PHASE_IDLE
            _star_stone_state.loop_count += 1


def _stop_star_stone_internal() -> None:
    with _star_stone_state.lock:
        _star_stone_state.running = False
        _star_stone_state.phase = _STAR_STONE_PHASE_IDLE
    _star_stone_state.stop_event.set()
    _emit_flow_status()


def start_star_stone_loop() -> dict[str, Any]:
    global _star_stone_thread
    if _star_stone_state.running:
        return {"ok": True}

    with _star_stone_state.lock:
        _star_stone_state.running = True
        _star_stone_state.phase = _STAR_STONE_PHASE_IDLE
        _star_stone_state.loop_count = 0
        _star_stone_state.total_stone = 0
        _star_stone_state.total_fragment = 0
        _star_stone_state.stop_event.clear()

    _emit_flow_log("star_stone", "开始执行获取升星石", level="ok")

    _star_stone_thread = threading.Thread(target=_run_star_stone_loop, daemon=True, name="star-stone-loop")
    _star_stone_thread.start()
    _emit_flow_status()
    return {"ok": True}


def stop_star_stone_loop() -> dict[str, Any]:
    _stop_star_stone_internal()
    _emit_flow_log("star_stone", "已手动停止获取升星石")
    return {"ok": True}


# =============================================================================
# Transport Supply
# =============================================================================
_TRANSPORT_SUPPLY_NPC_ID_PLACEHOLDER = "74010000"
_TRANSPORT_SUPPLY_BUY_ITEM_CODE = "022c9d09000000"
_TRANSPORT_SUPPLY_COMPLETION_FP_PREFIX = "e8030100e60"
_TRANSPORT_SUPPLY_REWARD_NAME = "通用10000储备金票"
_TRANSPORT_SUPPLY_MAX_ROUNDS = 10
_TRANSPORT_SUPPLY_POST_DELIVER_WAIT_MS = 180000
_TRANSPORT_SUPPLY_DELIVER_RETRY_MS = 5000
_TRANSPORT_SUPPLY_WAIT_BUY_MS = 65000


def _build_transport_deliver_hex(npc_id: str) -> str:
    import random

    rand = random.randint(0, 0xFFFF).to_bytes(2, "little").hex()
    template = (
        f"27000000e8030d00fe03cea8f5051004000015000000{_TRANSPORT_SUPPLY_NPC_ID_PLACEHOLDER}"
        "02319d0900000000000000000000000000"
    )
    return template.replace(_TRANSPORT_SUPPLY_NPC_ID_PLACEHOLDER, npc_id.lower()).replace("cea8", rand)


def _is_transport_completion(fp: str) -> bool:
    return _TRANSPORT_SUPPLY_COMPLETION_FP_PREFIX in fp.lower()


def _find_reward_item() -> dict[str, Any] | None:
    session = get_session()
    items = session.get_backpack_list()
    for item in items:
        if _TRANSPORT_SUPPLY_REWARD_NAME in (item.get("name") or "") and (item.get("quantity") or 0) > 0:
            return item
    return None


class _TransportSupplyState:
    def __init__(self):
        self.running = False
        self.stop_event = threading.Event()
        self.pending_reward_uses = 0
        self.lock = threading.Lock()


_transport_supply_state = _TransportSupplyState()
_transport_supply_thread: threading.Thread | None = None


def get_transport_supply_status() -> dict[str, Any]:
    with _transport_supply_state.lock:
        return {
            "ok": True,
            "running": _transport_supply_state.running,
            "pending_reward_uses": _transport_supply_state.pending_reward_uses,
        }


def _emit_transport_log(message: str, *, level: str = "info"):
    _emit_flow_log("transport_supply", message, level=level)


def _try_auto_use_reward_once() -> dict[str, Any]:
    item = _find_reward_item()
    if not item:
        return {"ok": False, "used": False, "reason": "not_found"}
    res = send_action("item.use", {"item_id": item["item_id"], "quantity": 1})
    if not res.get("ok"):
        return {"ok": False, "used": False, "reason": "use_failed", "error": res.get("error")}
    return {"ok": True, "used": True, "item_name": item.get("name", "")}


def _wait_with_reward_poll(wait_ms: int, round_num: int) -> bool:
    step = 500
    left = max(0, wait_ms)
    retry_left_ms = 0
    logged_not_found = False
    while left > 0:
        if _transport_supply_state.stop_event.wait(timeout=0):
            return False
        if _transport_supply_state.pending_reward_uses > 0:
            if retry_left_ms <= 0:
                res = _try_auto_use_reward_once()
                if res["ok"] and res["used"]:
                    _transport_supply_state.pending_reward_uses = max(0, _transport_supply_state.pending_reward_uses - 1)
                    logged_not_found = False
                    _emit_transport_log(
                        f"第 {round_num} 轮等待中：已自动使用奖励「{res['item_name']}」×1",
                        level="ok",
                    )
                    retry_left_ms = 300
                elif res["reason"] == "not_found":
                    if not logged_not_found:
                        _emit_transport_log(f"第 {round_num} 轮等待中：未在背包找到「{_TRANSPORT_SUPPLY_REWARD_NAME}」", level="info")
                        logged_not_found = True
                    retry_left_ms = 1200
                else:
                    _emit_transport_log(
                        f"第 {round_num} 轮等待中：自动使用奖励失败（{res.get('error', '未知错误')}）", level="err"
                    )
                    retry_left_ms = 1200
        time.sleep(min(step, left) / 1000.0)
        left -= min(step, left)
        retry_left_ms = max(0, retry_left_ms - step)
    return not _transport_supply_state.stop_event.is_set()


def _run_transport_supply_loop() -> None:
    global _transport_supply_thread
    _emit_transport_log("运输物资流程已启动（最多 10 轮）", level="ok")

    for round_num in range(1, _TRANSPORT_SUPPLY_MAX_ROUNDS + 1):
        if _transport_supply_state.stop_event.wait(timeout=0):
            break

        session = get_session()
        npc_id = ""
        with session._lock:
            npc_id = session.current_map_npc_id_hex
        if not npc_id or len(npc_id) != 8:
            _emit_transport_log("缺少当前地图 NPC id，已中止", level="err")
            break

        _emit_transport_log(f"第 {round_num} 轮：发送购买物资（NPC {npc_id}）…", level="info")
        buy_res = send_action("item.buy", {"npc_id": npc_id, "item_code": _TRANSPORT_SUPPLY_BUY_ITEM_CODE})
        if not buy_res.get("ok"):
            _emit_transport_log(f"购买发送失败：{buy_res.get('error', '未知错误')}", level="err")
            break

        time.sleep(0.8)
        if _transport_supply_state.stop_event.wait(timeout=0):
            break

        _emit_transport_log("等待 65 秒后交付物资…（期间自动使用上一轮奖励）", level="info")
        waited = _wait_with_reward_poll(_TRANSPORT_SUPPLY_WAIT_BUY_MS, round_num)
        if not waited:
            _emit_transport_log("已停止（等待交付阶段中断）", level="info")
            break

        deliver_hex = _build_transport_deliver_hex(npc_id)
        _emit_transport_log(f"发送交付物资（NPC {npc_id}）…", level="info")

        deadline = time.time() + _TRANSPORT_SUPPLY_POST_DELIVER_WAIT_MS / 1000.0
        done = False
        while time.time() < deadline:
            if _transport_supply_state.stop_event.wait(timeout=0):
                break
            send_res = send_action("probe.send", {"hex": deliver_hex, "use_queue": True})
            if not send_res.get("ok"):
                _emit_transport_log(f"交付发送失败：{send_res.get('error', '未知错误')}", level="err")
                break
            time.sleep(_TRANSPORT_SUPPLY_DELIVER_RETRY_MS / 1000.0)

            packet_log = get_session().get_packet_log(limit=50, direction="DN")
            for rec in reversed(packet_log):
                fp = rec.get("fingerprint") or ""
                if _is_transport_completion(fp):
                    done = True
                    break
            if done:
                break

        if _transport_supply_state.stop_event.wait(timeout=0):
            break

        if done:
            _emit_transport_log(f"第 {round_num} 轮：完成", level="ok")
        else:
            _emit_transport_log(f"第 {round_num} 轮：等待完成响应超时", level="err")
            break

        _transport_supply_state.pending_reward_uses += 1
        if round_num == _TRANSPORT_SUPPLY_MAX_ROUNDS:
            last_res = _try_auto_use_reward_once()
            if last_res["ok"] and last_res["used"]:
                _transport_supply_state.pending_reward_uses = max(0, _transport_supply_state.pending_reward_uses - 1)
                _emit_transport_log(f"最后一轮奖励已自动使用：{last_res['item_name']}×1", level="ok")
            elif last_res["reason"] == "not_found":
                _emit_transport_log(f"最后一轮未在背包找到「{_TRANSPORT_SUPPLY_REWARD_NAME}」", level="err")
        else:
            _emit_transport_log(f"第 {round_num} 轮奖励入队：下一轮等待阶段自动使用", level="info")

    with _transport_supply_state.lock:
        if _transport_supply_state.pending_reward_uses > 0:
            _emit_transport_log(f"流程结束：仍有 {_transport_supply_state.pending_reward_uses} 张奖励票未自动使用", level="err")
        else:
            _emit_transport_log("流程结束", level="info")
    _emit_flow_status()


def start_transport_supply() -> dict[str, Any]:
    global _transport_supply_thread
    if _transport_supply_state.running:
        return {"ok": True}

    with _transport_supply_state.lock:
        _transport_supply_state.running = True
        _transport_supply_state.pending_reward_uses = 0
        _transport_supply_state.stop_event.clear()

    _transport_supply_thread = threading.Thread(target=_run_transport_supply_loop, daemon=True, name="transport-supply-loop")
    _transport_supply_thread.start()
    _emit_flow_status()
    return {"ok": True}


def stop_transport_supply() -> dict[str, Any]:
    with _transport_supply_state.lock:
        _transport_supply_state.running = False
    _transport_supply_state.stop_event.set()
    _emit_transport_log("已请求停止…", level="info")
    _emit_flow_status()
    return {"ok": True}


# =============================================================================
# Liaoguo
# =============================================================================
_LIAOGUO_E207_SETTLEMENT_TIMEOUT_S = 120
_LIAOGUO_PAIR_WAIT_TIMEOUT_S = 20


class _LiaoguoState:
    def __init__(self):
        self.running = False
        self.stop_event = threading.Event()
        self.lock = threading.Lock()


_liaoguo_state = _LiaoguoState()
_liaoguo_thread: threading.Thread | None = None


def get_liaoguo_status() -> dict[str, Any]:
    with _liaoguo_state.lock:
        return {"ok": True, "running": _liaoguo_state.running}


def _build_abandon_task_packet(abandon_code: str) -> str:
    import random

    r = random.randint(0, 0xFFFF).to_bytes(2, "little").hex()
    return f"18000000e80306000004{r}f5050304000006000000{abandon_code}07000000"


def _wait_for_fingerprint(target_fp: str, timeout_s: float = 20.0) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _liaoguo_state.stop_event.is_set():
            return {"ok": False, "reason": "stopped"}
        packet_log = get_session().get_packet_log(limit=100, direction="DN")
        for rec in reversed(packet_log):
            fp = (rec.get("fingerprint") or "").lower()
            if target_fp.lower() in fp:
                return {"ok": True, "record": rec}
        time.sleep(0.2)
    return {"ok": False, "reason": "timeout"}


def _emit_liaoguo_log(message: str, *, level: str = "info"):
    _emit_flow_log("liaoguo", message, level=level)


def _run_liaoguo_flow(pair: dict[str, Any]) -> None:
    global _liaoguo_thread
    item_code = pair.get("itemCode", "")
    monster_code = pair.get("monsterCode", "")
    label = pair.get("label", "")
    task_name = pair.get("taskName", "")
    ticket_item_code = pair.get("ticketItemCode", "")
    abandon_task_code = pair.get("abandonTaskCode", "21a1")

    _emit_liaoguo_log(f"开始：{item_code} - {label} - {monster_code} - {task_name}", level="ok")

    session = get_session()
    npc_id = ""
    with session._lock:
        npc_id = session.current_map_npc_id_hex
    if not npc_id:
        _emit_liaoguo_log("缺少当前地图 NPC id", level="err")
        _emit_flow_status()
        return

    _emit_liaoguo_log(f"步骤1/4 兑换任务券（NPC {npc_id}）…", level="info")
    buy_res = send_action("item.buy", {"npc_id": npc_id, "item_code": item_code})
    if not buy_res.get("ok"):
        _emit_liaoguo_log(f"兑换任务券失败：{buy_res.get('error', '未知错误')}", level="err")
        _emit_flow_status()
        return

    ack = _wait_for_fingerprint("e8030100e607", _LIAOGUO_PAIR_WAIT_TIMEOUT_S)
    if _liaoguo_state.stop_event.is_set():
        _emit_flow_status()
        return
    if not ack["ok"]:
        _emit_liaoguo_log("未在时限内收到购买成功响应 e8030100e607", level="err")
        _emit_flow_status()
        return
    _emit_liaoguo_log("已收到购买成功响应 e8030100e607", level="ok")

    abandon_hex = _build_abandon_task_packet(abandon_task_code)
    _emit_liaoguo_log(f"步骤2/4 放弃任务（code {abandon_task_code}）…", level="info")
    abandon_res = send_action("probe.send", {"hex": abandon_hex, "use_queue": True})
    if not abandon_res.get("ok"):
        _emit_liaoguo_log(f"放弃任务失败：{abandon_res.get('error', '未知错误')}", level="err")
        _emit_flow_status()
        return

    ack = _wait_for_fingerprint("e8030100e807", _LIAOGUO_PAIR_WAIT_TIMEOUT_S)
    if _liaoguo_state.stop_event.is_set():
        _emit_flow_status()
        return
    if not ack["ok"]:
        _emit_liaoguo_log("未在时限内收到放弃任务响应 e8030100e807", level="err")
        _emit_flow_status()
        return
    _emit_liaoguo_log("已收到放弃任务响应 e8030100e807", level="ok")

    _emit_liaoguo_log(f"步骤3/4 使用任务券（{ticket_item_code}）…", level="info")
    use_res = send_action("item.use", {"item_code": ticket_item_code, "quantity": 1})
    if not use_res.get("ok"):
        _emit_liaoguo_log(f"使用任务券失败：{use_res.get('error', '未知错误')}", level="err")
        _emit_flow_status()
        return

    ack = _wait_for_fingerprint("e8030100ec07", _LIAOGUO_PAIR_WAIT_TIMEOUT_S)
    if _liaoguo_state.stop_event.is_set():
        _emit_flow_status()
        return
    if not ack["ok"]:
        _emit_liaoguo_log("未在时限内收到已接受任务响应 e8030100ec07", level="err")
        _emit_flow_status()
        return
    _emit_liaoguo_log("已收到已接受任务响应 e8030100ec07", level="ok")

    _emit_liaoguo_log(f"步骤4/4 辽国战斗（怪物 {monster_code}），等待结算 e8030100e207…", level="info")
    battle_res = send_action("battle.start", {"monster_code": monster_code, "run_pre_battle_actions": True})
    if not battle_res.get("ok"):
        _emit_liaoguo_log(f"战斗启动失败：{battle_res.get('error', '未知错误')}", level="err")
        _emit_flow_status()
        return

    settle = _wait_for_fingerprint("e8030100e207", _LIAOGUO_E207_SETTLEMENT_TIMEOUT_S)
    if _liaoguo_state.stop_event.is_set():
        _emit_flow_status()
        return
    if not settle["ok"]:
        _emit_liaoguo_log("未在时限内收到战斗结算 e8030100e207", level="err")
        _emit_flow_status()
        return
    _emit_liaoguo_log("已收到 e8030100e207，流程完成", level="ok")
    _emit_flow_status()


def start_liaoguo(pair: dict[str, Any]) -> dict[str, Any]:
    global _liaoguo_thread
    if _liaoguo_state.running:
        return {"ok": True}

    with _liaoguo_state.lock:
        _liaoguo_state.running = True
        _liaoguo_state.stop_event.clear()

    def _liaoguo_worker():
        try:
            _run_liaoguo_flow(pair)
        finally:
            with _liaoguo_state.lock:
                _liaoguo_state.running = False
            _emit_flow_status()

    _liaoguo_thread = threading.Thread(target=_liaoguo_worker, daemon=True, name="liaoguo-loop")
    _liaoguo_thread.start()
    _emit_flow_status()
    return {"ok": True}


def stop_liaoguo() -> dict[str, Any]:
    with _liaoguo_state.lock:
        _liaoguo_state.running = False
    _liaoguo_state.stop_event.set()
    _emit_liaoguo_log("已请求停止…", level="info")
    _emit_flow_status()
    return {"ok": True}
