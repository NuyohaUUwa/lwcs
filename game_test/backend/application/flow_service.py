"""HTTP-facing complex flow façade (star-stone, transport-supply, liaoguo, synthesis-batch)."""

import queue
import threading
import time
from typing import Any

from game_test.backend.domain.inventory.item_use import (
    build_synthesize_packet,
    get_current_map_npc_id,
    normalize_backpack_item_id_12,
)
from game_test.backend.runtime import get_session
from game_test.backend.runtime.actions import send_action, send_raw_action


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
    with _synthesis_batch_state.lock:
        sb_running = _synthesis_batch_state.running
    session._notify_sse("flow_status", {
        "star_stone_running": ss_running,
        "transport_supply_running": ts_running,
        "liaoguo_running": lg_running,
        "synthesis_batch_running": sb_running,
    })


# =============================================================================
# Star Stone Loop
# =============================================================================
_STAR_STONE_TARGET_NAME_RE = "特步"
_STAR_STONE_LOOP_INTERVAL_S = 1.0
# 购买后等背包落特步类物品再分解（等 d607/ec07），秒
_STAR_STONE_BACKPACK_WAIT_S = 6.0
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


def _find_backpack_item_id_for_star_stone() -> str | None:
    """名称含「特步」的背包格（与购买配置一致，用于买后分解）。"""
    session = get_session()
    with session._lock:
        for it in session.backpack_items.values():
            if _STAR_STONE_TARGET_NAME_RE in (it.name or ""):
                return it.item_id
    return None


def _wait_backpack_item_id_for_star_stone(*, timeout_s: float) -> str | None:
    deadline = time.time() + max(0.1, timeout_s)
    while time.time() < deadline:
        if _star_stone_state.stop_event.is_set():
            return None
        iid = _find_backpack_item_id_for_star_stone()
        if iid:
            return iid
        time.sleep(0.2)
    return None


def _run_star_stone_loop() -> None:
    global _star_stone_thread
    first_round = True
    while True:
        if not first_round:
            if _star_stone_state.stop_event.wait(timeout=_STAR_STONE_LOOP_INTERVAL_S):
                _emit_flow_status()
                return
        first_round = False
        if _star_stone_state.stop_event.is_set():
            _emit_flow_status()
            return

        favorite = _find_star_stone_buy_favorite()
        if not favorite:
            _emit_flow_log("star_stone", "未找到常用购买物品（名称含「特步」，如特步鞋），请「保存常用」", level="err")
            _stop_star_stone_internal()
            return

        npc_id = get_current_map_npc_id()

        n_next = 0
        with _star_stone_state.lock:
            n_next = _star_stone_state.loop_count + 1
            _star_stone_state.phase = _STAR_STONE_PHASE_BUY

        res = send_action("item.buy", {"npc_id": npc_id, "item_code": favorite.get("code", "")})
        if not res.get("ok"):
            _emit_flow_log("star_stone", f"购买失败: {res.get('error', '未知错误')}", level="err")
            _stop_star_stone_internal()
            return

        _emit_flow_log("star_stone", f"第{n_next}轮：已发购买，等待入包以分解…", level="info")

        item_id = _wait_backpack_item_id_for_star_stone(timeout_s=_STAR_STONE_BACKPACK_WAIT_S)
        if not item_id:
            if _star_stone_state.stop_event.is_set():
                _emit_flow_status()
                return
            with _star_stone_state.lock:
                _star_stone_state.phase = _STAR_STONE_PHASE_IDLE
                _star_stone_state.loop_count += 1
            _emit_flow_log(
                "star_stone",
                f"第{n_next}轮：{int(_STAR_STONE_BACKPACK_WAIT_S)}s 内未在背包发现特步类物品，已跳过本次分解",
                level="warn",
            )
            continue

        with _star_stone_state.lock:
            _star_stone_state.phase = _STAR_STONE_PHASE_DECOMPOSE
        dres = send_action("item.decompose", {"item_id": item_id})
        with _star_stone_state.lock:
            _star_stone_state.phase = _STAR_STONE_PHASE_IDLE
        if not dres.get("ok"):
            with _star_stone_state.lock:
                _star_stone_state.loop_count += 1
            _emit_flow_log(
                "star_stone",
                f"第{n_next}轮：分解失败 — {dres.get('error', '未知错误')}",
                level="err",
            )
            continue

        with _star_stone_state.lock:
            _star_stone_state.total_stone += 11
            _star_stone_state.total_fragment += 23
            _star_stone_state.loop_count += 1
            stone = _star_stone_state.total_stone
            frag = _star_stone_state.total_fragment
        _emit_flow_log(
            "star_stone",
            f"第{n_next}轮：已分解，累计获得：升星石 {stone}、宝石碎片 {frag}",
            level="ok",
        )


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
_TRANSPORT_SUPPLY_BUY_ACK_FP = "e8030100e607"
_TRANSPORT_SUPPLY_BUY_ACK_TIMEOUT_S = 30.0
_TRANSPORT_SUPPLY_BUY_FAIL_TEXT = "领取失败"
_TRANSPORT_SUPPLY_ALREADY_ACTIVE_TEXT = "同时只能领取一次任务"


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


def _packet_record_utf8(rec: dict[str, Any]) -> str:
    parsed = rec.get("parsed")
    if isinstance(parsed, dict):
        return str(parsed.get("utf8_text") or "")
    return ""


def _is_transport_buy_success_message(text: str) -> bool:
    """购买成功：下行文案需同时含「1000金消失了」「获得」「5级物资」。"""
    if not text:
        return False
    compact = text.replace("\r", "").replace("\n", "")
    return "1000金消失了" in compact and "获得" in compact and "5级物资" in compact


def _wait_for_transport_buy_dn_ack(*, marker_id: int, timeout_s: float) -> dict[str, Any]:
    """等待购买后的 e8030100e607 下行，并返回首条服务器文案。"""
    deadline = time.time() + timeout_s
    target = _TRANSPORT_SUPPLY_BUY_ACK_FP.lower()
    logged_unknown: set[int] = set()
    while time.time() < deadline:
        if _transport_supply_state.stop_event.wait(timeout=0):
            return {"ok": False, "reason": "stopped"}
        packet_log = get_session().get_packet_log(limit=200, direction="DN")
        rows = sorted(
            (r for r in packet_log if int(r.get("id") or 0) > marker_id),
            key=lambda r: int(r.get("id") or 0),
        )
        for rec in rows:
            fp = (rec.get("fingerprint") or "").lower()
            if target not in fp:
                continue
            rid = int(rec.get("id") or 0)
            text = _packet_record_utf8(rec)
            if _TRANSPORT_SUPPLY_BUY_FAIL_TEXT in text:
                return {"ok": False, "reason": "claim_failed", "utf8_text": text, "record_id": rid}
            if _is_transport_buy_success_message(text):
                return {"ok": True, "utf8_text": text, "record_id": rid}
            if rid not in logged_unknown:
                logged_unknown.add(rid)
                _emit_transport_log(
                    f"购买响应未匹配预期文案，继续等待本轮确认。服务端原文：{text if text else '(无文本)'}",
                    level="info",
                )
        time.sleep(0.2)
    return {"ok": False, "reason": "timeout"}


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

        npc_id = get_current_map_npc_id()

        _emit_transport_log(f"第 {round_num} 轮：发送购买物资（NPC {npc_id}）…", level="info")
        marker_id = _latest_dn_packet_id()
        buy_res = send_action("item.buy", {"npc_id": npc_id, "item_code": _TRANSPORT_SUPPLY_BUY_ITEM_CODE})
        if not buy_res.get("ok"):
            _emit_transport_log(f"购买发送失败：{buy_res.get('error', '未知错误')}", level="err")
            continue

        _emit_transport_log(
            f"等待购买结果下行（指纹 {_TRANSPORT_SUPPLY_BUY_ACK_FP}），最长 {_TRANSPORT_SUPPLY_BUY_ACK_TIMEOUT_S:.0f}s…",
            level="info",
        )
        buy_ack = _wait_for_transport_buy_dn_ack(
            marker_id=marker_id, timeout_s=_TRANSPORT_SUPPLY_BUY_ACK_TIMEOUT_S
        )
        if buy_ack.get("reason") == "stopped":
            _emit_transport_log("已停止（等待购买响应阶段中断）", level="info")
            break
        if not buy_ack.get("ok"):
            raw_text = str(buy_ack.get("utf8_text") or "")
            raw_direct = raw_text if raw_text else "(无文本)"
            already_active = (
                buy_ack.get("reason") == "claim_failed"
                and _TRANSPORT_SUPPLY_ALREADY_ACTIVE_TEXT in raw_text
            )
            if already_active:
                _emit_transport_log(
                    f"服务端报「已有任务」，视为已购买，继续交付。服务端原文：{raw_direct}",
                    level="warn",
                )
            else:
                if buy_ack.get("reason") == "claim_failed":
                    _emit_transport_log(
                        f"购买失败：服务端原文：{raw_direct}",
                        level="err",
                    )
                elif buy_ack.get("reason") == "timeout":
                    _emit_transport_log(
                        f"购买确认超时：{_TRANSPORT_SUPPLY_BUY_ACK_TIMEOUT_S:.0f}s 内未收到 {_TRANSPORT_SUPPLY_BUY_ACK_FP}",
                        level="err",
                    )
                else:
                    _emit_transport_log(f"购买确认失败：{buy_ack.get('reason', '未知')}。服务端原文：{raw_direct}", level="err")
                continue
        else:
            ok_text = str(buy_ack.get("utf8_text") or "")
            _emit_transport_log(
                f"购买成功：服务端原文：{ok_text if ok_text else '(无文本)'}",
                level="ok",
            )

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
        _transport_supply_state.running = False
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


def _latest_dn_packet_id() -> int:
    packet_log = get_session().get_packet_log(limit=1, direction="DN")
    if not packet_log:
        return 0
    try:
        return int(packet_log[-1].get("id") or 0)
    except Exception:
        return 0


def _wait_for_fingerprint(target_fp: str, timeout_s: float = 20.0, *, min_record_id: int = 0) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    target = target_fp.lower()
    while time.time() < deadline:
        if _liaoguo_state.stop_event.is_set():
            return {"ok": False, "reason": "stopped"}
        packet_log = get_session().get_packet_log(limit=200, direction="DN")
        for rec in reversed(packet_log):
            rec_id = int(rec.get("id") or 0)
            if rec_id <= min_record_id:
                # 只接受本步骤发送之后的新下行，避免误命中历史包导致流程看起来乱序
                continue
            fp = (rec.get("fingerprint") or "").lower()
            if target in fp:
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

    npc_id = get_current_map_npc_id()

    _emit_liaoguo_log(f"步骤1/4 兑换任务券（NPC {npc_id}）…", level="info")
    marker_id = _latest_dn_packet_id()
    buy_res = send_action("item.buy", {"npc_id": npc_id, "item_code": item_code})
    if not buy_res.get("ok"):
        _emit_liaoguo_log(f"兑换任务券失败：{buy_res.get('error', '未知错误')}", level="err")
        _emit_flow_status()
        return

    ack = _wait_for_fingerprint("e8030100e607", _LIAOGUO_PAIR_WAIT_TIMEOUT_S, min_record_id=marker_id)
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
    marker_id = _latest_dn_packet_id()
    abandon_res = send_action("probe.send", {"hex": abandon_hex, "use_queue": True})
    if not abandon_res.get("ok"):
        _emit_liaoguo_log(f"放弃任务失败：{abandon_res.get('error', '未知错误')}", level="err")
        _emit_flow_status()
        return

    ack = _wait_for_fingerprint("e8030100e807", _LIAOGUO_PAIR_WAIT_TIMEOUT_S, min_record_id=marker_id)
    if _liaoguo_state.stop_event.is_set():
        _emit_flow_status()
        return
    if not ack["ok"]:
        _emit_liaoguo_log("未在时限内收到放弃任务响应 e8030100e807", level="err")
        _emit_flow_status()
        return
    _emit_liaoguo_log("已收到放弃任务响应 e8030100e807", level="ok")

    _emit_liaoguo_log(f"步骤3/4 使用任务券（{ticket_item_code}）…", level="info")
    marker_id = _latest_dn_packet_id()
    use_res = send_action("item.use", {"item_code": ticket_item_code, "quantity": 1})
    if not use_res.get("ok"):
        _emit_liaoguo_log(f"使用任务券失败：{use_res.get('error', '未知错误')}", level="err")
        _emit_flow_status()
        return

    ack = _wait_for_fingerprint("e8030100ec07", _LIAOGUO_PAIR_WAIT_TIMEOUT_S, min_record_id=marker_id)
    if _liaoguo_state.stop_event.is_set():
        _emit_flow_status()
        return
    if not ack["ok"]:
        _emit_liaoguo_log("未在时限内收到已接受任务响应 e8030100ec07", level="err")
        _emit_flow_status()
        return
    _emit_liaoguo_log("已收到已接受任务响应 e8030100ec07", level="ok")

    _emit_liaoguo_log(f"步骤4/4 辽国战斗（怪物 {monster_code}），等待结算 e8030100e207…", level="info")
    marker_id = _latest_dn_packet_id()
    battle_res = send_action("battle.start", {"monster_code": monster_code, "run_pre_battle_actions": True})
    if not battle_res.get("ok"):
        _emit_liaoguo_log(f"战斗启动失败：{battle_res.get('error', '未知错误')}", level="err")
        _emit_flow_status()
        return

    settle = _wait_for_fingerprint("e8030100e207", _LIAOGUO_E207_SETTLEMENT_TIMEOUT_S, min_record_id=marker_id)
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


# =============================================================================
# 一键合成：连续发包直至 e80301004f51 返回「数量不足」
# =============================================================================
_synthesis_batch_result_q: "queue.Queue[dict[str, Any]]" = queue.Queue()


class _SynthesisBatchState:
    def __init__(self) -> None:
        self.running: bool = False
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.expect: bool = False
        self.finish_sent: bool = False
        self.item_id: str = ""


_synthesis_batch_state = _SynthesisBatchState()
_synthesis_batch_thread: threading.Thread | None = None


def _drain_synthesis_batch_queue() -> None:
    while True:
        try:
            _synthesis_batch_result_q.get_nowait()
        except queue.Empty:
            break


def deliver_synthesis_batch_response(parsed: dict[str, Any]) -> bool:
    """
    收包线程：若一键合成正等待 e80301004f51，将解析结果交给工作线程，并返回 True
    （此时不单独推 synthesis_result，避免与批量日志重复）。
    """
    st = _synthesis_batch_state
    with st.lock:
        if not (st.running and st.expect):
            return False
    try:
        _synthesis_batch_result_q.put_nowait(dict(parsed))
    except Exception:
        return False
    with st.lock:
        st.expect = False
    return True


def _synthesis_batch_wait_response(timeout_s: float) -> dict[str, Any] | None:
    st = _synthesis_batch_state
    deadline = time.time() + max(0.1, timeout_s)
    while time.time() < deadline:
        if st.stop_event.is_set():
            with st.lock:
                st.expect = False
            return None
        try:
            return _synthesis_batch_result_q.get(timeout=0.2)
        except queue.Empty:
            pass
    with st.lock:
        st.expect = False
    return None


def _notify_synthesis_batch_finished(
    reason: str,
    ok: int,
    fail: int,
    detail: str = "",
) -> None:
    st = _synthesis_batch_state
    with st.lock:
        if st.finish_sent:
            return
        st.finish_sent = True
    session = get_session()
    session._notify_sse(
        "synthesis_batch",
        {
            "state": "finished",
            "reason": reason,
            "ok": ok,
            "fail": fail,
            "message": detail,
        },
    )
    session.notify_backpack_update()


def _synthesis_batch_worker(item_id: str) -> None:
    st = _synthesis_batch_state
    success = 0
    fail = 0
    try:
        while not st.stop_event.is_set():
            session = get_session()
            with session._lock:
                if item_id not in session.backpack_items:
                    _emit_flow_log("synthesis_batch", "物品已不在背包，已停止", level="err")
                    _notify_synthesis_batch_finished("not_in_backpack", success, fail, "物品已不在背包")
                    return
            _drain_synthesis_batch_queue()
            with st.lock:
                st.expect = True
            res = send_raw_action(build_synthesize_packet(item_id), priority=0, use_queue=True)
            if not res.get("ok"):
                err = str(res.get("error", "发送失败"))
                _emit_flow_log("synthesis_batch", f"发送失败：{err}", level="err")
                _notify_synthesis_batch_finished("send_error", success, fail, err)
                return
            resp = _synthesis_batch_wait_response(30.0)
            if resp is None:
                if st.stop_event.is_set():
                    _emit_flow_log("synthesis_batch", "已按请求停止", level="info")
                    _notify_synthesis_batch_finished("user", success, fail, "")
                else:
                    _emit_flow_log("synthesis_batch", "等待合成响应超时", level="err")
                    _notify_synthesis_batch_finished("timeout", success, fail, "等待响应超时")
                return
            o = str(resp.get("outcome", ""))
            msg = str(resp.get("message", ""))
            round_n = success + fail + 1
            if o == "insufficient":
                _emit_flow_log("synthesis_batch", f"第{round_n} 次：{msg}（材料不足，流程结束）", level="ok")
                _notify_synthesis_batch_finished("insufficient", success, fail, msg)
                return
            if o == "ok":
                success += 1
            elif o == "fail":
                fail += 1
            _emit_flow_log(
                "synthesis_batch",
                f"第{round_n} 次：{msg}（累计 成功{success} / 失败{fail}）",
                level="info",
            )
    except Exception as e:
        _emit_flow_log("synthesis_batch", f"异常：{e}", level="err")
        _notify_synthesis_batch_finished("error", success, fail, str(e))
    finally:
        with st.lock:
            was_sent = st.finish_sent
            st.running = False
            st.expect = False
        _drain_synthesis_batch_queue()
        if not was_sent:
            _notify_synthesis_batch_finished("error", success, fail, "未能正常结束")
        _emit_flow_status()


def get_synthesis_batch_status() -> dict[str, Any]:
    with _synthesis_batch_state.lock:
        return {
            "ok": True,
            "running": _synthesis_batch_state.running,
            "item_id": _synthesis_batch_state.item_id,
        }


def start_synthesis_batch(item_id: str) -> dict[str, Any]:
    global _synthesis_batch_thread
    if _synthesis_batch_state.running:
        return {"ok": False, "error": "一键合成正在运行"}
    if not str(item_id or "").strip():
        return {"ok": False, "error": "item_id 不能为空"}
    try:
        iid = normalize_backpack_item_id_12(item_id)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    session = get_session()
    if not session.sock or not session.connected:
        return {"ok": False, "error": "未连接游戏服"}
    with session._lock:
        if iid not in session.backpack_items:
            return {"ok": False, "error": "背包中不存在该物品"}

    with _synthesis_batch_state.lock:
        _synthesis_batch_state.running = True
        _synthesis_batch_state.stop_event.clear()
        _synthesis_batch_state.expect = False
        _synthesis_batch_state.finish_sent = False
        _synthesis_batch_state.item_id = iid
    _drain_synthesis_batch_queue()
    _emit_flow_log("synthesis_batch", f"开始一键合成（{iid}）", level="ok")
    _synthesis_batch_thread = threading.Thread(
        target=_synthesis_batch_worker, args=(iid,), daemon=True, name="synthesis-batch"
    )
    _synthesis_batch_thread.start()
    session._notify_sse("synthesis_batch", {"state": "started", "item_id": iid})
    _emit_flow_status()
    return {"ok": True, "item_id": iid}


def stop_synthesis_batch() -> dict[str, Any]:
    _synthesis_batch_state.stop_event.set()
    _emit_flow_log("synthesis_batch", "已请求停止…", level="info")
    _emit_flow_status()
    return {"ok": True}
