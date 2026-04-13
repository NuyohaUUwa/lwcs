"""
战斗功能：
1) 客户端发起战斗（f603）
2) 服务端状态机驱动后续战斗（f703）
3) 解析服务器战斗响应（de07，仅推进回合，不以 de07 判定战斗结束）
4) 解析 df07：进行中（嵌 e207 片段）则继续发 f703；内力不足则停止；「战斗已结束」仅作提示，胜负以 e207 为准
5) 解析 e8030100e207：根据「获得」「失去」判定胜负并结束本回合；胜则循环下一轮，败则停
"""

import json
import re
import time
from typing import Any, Dict, List

from config import DEFAULT_BATTLE_LOOP_DELAY_MS
from core.session import get_session
from paths import AUTO_USE_RULES_FILE, MONSTERS_FILE
from features import item_use
from features.role_stats import update_session_stats
from utils.random_num import random_num_hex4

# 发起战斗模板：{seq} 为 4 位 hex，{monster} 为 4 位怪物代码
_BATTLE_START_TEMPLATE = "1b000000e8030500f603{seq}f505fc030000090000000100{monster}0000000000"

# 进行战斗（群体技能）包
_BATTLE_SKILL_PACKET_TEMPLATE = (
    "22000000e8030500f703{random_num}f50504040000100000000100{skill_job_hex}00000000000000030001020000"
)

BATTLE_STATE_IDLE = "idle"
BATTLE_STATE_WAITING_START_RESPONSE = "waiting_start_response"
BATTLE_STATE_WAITING_ACTION_RESULT = "waiting_action_result"
BATTLE_STATE_ENDED = "ended"
BATTLE_STATE_ERROR = "error"

DE07_TIMEOUT_S = 3.0
F703_TIMEOUT_S = 3.0
BATTLE_WAIT_TIMEOUT_GRACE_S = 0.5

# 战斗 de07 下行全包 hex 需包含此子串才视为目标响应，并清零 f703 超时补发计数
BATTLE_DE07_HEX_MARK = "030100de07"
MAX_F703_TIMEOUT_RECOVER = 3


def _load_default_monsters() -> list:
    """从 paths.MONSTERS_FILE 加载默认怪物列表。"""
    try:
        with open(MONSTERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            out = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                code = str(item.get("code", "")).strip().lower()
                if name and len(code) == 4:
                    try:
                        int(code, 16)
                        out.append({"name": name, "code": code})
                    except ValueError:
                        continue
            return out
    except Exception:
        pass
    return []


# 默认怪物列表（优先来自 JSON，前端可增删）
DEFAULT_MONSTERS = _load_default_monsters()
_BATTLE_END_HEX_TOKENS = (
    "e88eb7e5be97e7bb8fe9aa8cefbc9a",  # 获得经验：
    "e88eb7e5be97e98791e5b8813a20",    # 获得金币:
)
_NEILI_NOT_ENOUGH_HEX_TOKEN = "e58685e58a9be4b88de8b6b3"  # 内力不足
_BATTLE_ENDED_HEX_TOKEN = "e68898e69697e5b7b2e7bb93e69d9f"  # 战斗已结束
# df07 体部「仍在战斗中、等待下一发 f703」的固定片段（见下行 df07 + 内嵌 e207  opcode）
_DF07_BODY_BATTLE_CONTINUES = "00000000e2070000"
_DURATION_STAT_KEYS = {"经验UP", "攻击UP", "金钱UP"}
_TELEPORT_TICKET_PACKET_TEMPLATE = (
    "12000000e80302000504{random_1}f5050204000000000000"
    "1c000000e80303003e28{random_2}f605452800000a0000005d000000000000000000"
)


def _normalize_hex4(value: str, name: str) -> str:
    v = (value or "").strip().lower()
    if len(v) != 4:
        raise ValueError(f"{name} 必须是 4 位 hex")
    try:
        int(v, 16)
    except ValueError as e:
        raise ValueError(f"{name} 不是合法 hex") from e
    return v


def _to_int(v) -> int:
    s = str(v or "").strip()
    m = re.search(r"-?\d+", s)
    return int(m.group(0)) if m else 0


def _parse_duration_minutes(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    hour_match = re.search(r"(\d+)\s*(?:时|小时)", text)
    minute_match = re.search(r"(\d+)\s*(?:分|分钟)", text)
    if hour_match or minute_match:
        hours = int(hour_match.group(1)) if hour_match else 0
        minutes = int(minute_match.group(1)) if minute_match else 0
        return hours * 60 + minutes
    return _to_int(text)


def _normalize_auto_use_rule(rule: Dict[str, Any]) -> Dict[str, Any]:
    rid = str(rule.get("id", "")).strip()
    return {
        "id": rid,
        "label": str(rule.get("label", rid)),
        "enabled": bool(rule.get("enabled", False)),
        "stat_key": str(rule.get("stat_key", "")),
        "threshold": int(rule.get("threshold", 0)),
        "item_name": str(rule.get("item_name", "")),
        "item_id": str(rule.get("item_id", "")).strip().lower(),
    }


def _parse_auto_use_rules_list(raw: List[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("id", "")).strip()
        if not rid:
            continue
        out.append(_normalize_auto_use_rule(r))
    return out


def _load_auto_use_rules() -> List[Dict[str, Any]]:
    try:
        with open(AUTO_USE_RULES_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, list):
            raise ValueError("rules must be list")
    except Exception:
        return []

    return _parse_auto_use_rules_list(raw)


def _save_auto_use_rules(rules: List[Dict[str, Any]]) -> None:
    with open(AUTO_USE_RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)


def get_auto_use_rules() -> List[Dict[str, Any]]:
    session = get_session()
    with session._lock:
        rules = getattr(session, "auto_use_rules", None)
        if rules is None:
            rules = _load_auto_use_rules()
            session.auto_use_rules = rules
            session.auto_use_last_ts = {}
            session.auto_use_pending_actions = []
        return [dict(x) for x in rules]


def set_auto_use_rules(updated_rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    new_rules: List[Dict[str, Any]] = []
    for r in updated_rules:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("id", "")).strip()
        if not rid:
            continue
        new_rules.append(_normalize_auto_use_rule(r))

    session = get_session()
    with session._lock:
        session.auto_use_rules = new_rules
        if not hasattr(session, "auto_use_last_ts"):
            session.auto_use_last_ts = {}
    _save_auto_use_rules(new_rules)
    return [dict(x) for x in new_rules]


def evaluate_auto_use(trigger: str = "") -> Dict[str, Any]:
    return prepare_auto_use_actions(trigger)


def prepare_auto_use_actions(trigger: str = "") -> Dict[str, Any]:
    session = get_session()
    if not session.connected:
        return {"ok": False, "error": "未连接"}

    with session._lock:
        rules = [dict(x) for x in getattr(session, "auto_use_rules", _load_auto_use_rules())]
        session.auto_use_rules = rules
        stats = dict(session.role_stats)
        items = {k: v.to_dict() for k, v in session.backpack_items.items()}
        last_ts = dict(getattr(session, "auto_use_last_ts", {}))
        pending_existing = {
            str(x.get("id", "")): dict(x)
            for x in getattr(session, "auto_use_pending_actions", [])
            if isinstance(x, dict) and x.get("id")
        }

    now = time.time()
    actions = []
    for rule in rules:
        if not rule.get("enabled"):
            continue
        if rule.get("id") == "battle_teleport_ticket":
            continue
        item_id = str(rule.get("item_id", "")).strip().lower()
        if not item_id:
            continue
        stat_key = rule.get("stat_key", "")
        threshold = int(rule.get("threshold", 0))
        stat_val = _parse_duration_minutes(stats.get(stat_key, 0)) if stat_key in _DURATION_STAT_KEYS else _to_int(stats.get(stat_key, 0))
        if stat_val >= threshold:
            continue
        if rule["id"] in pending_existing:
            continue
        item = items.get(item_id)
        if not item or int(item.get("quantity", 0)) <= 0:
            actions.append({"id": rule["id"], "ok": False, "reason": "背包无可用物品", "phase": "prepared"})
            continue
        if now - float(last_ts.get(rule["id"], 0.0)) < 3.0:
            continue

        prepared = {
            "id": rule["id"],
            "ok": True,
            "item_id": item_id,
            "item_name": rule.get("item_name", ""),
            "stat_key": stat_key,
            "stat_value": stat_val,
            "threshold": threshold,
            "phase": "prepared",
            "prepared_ts": now,
        }
        actions.append(prepared)
        pending_existing[rule["id"]] = prepared

    with session._lock:
        session.auto_use_last_ts = last_ts
        session.auto_use_pending_actions = list(pending_existing.values())

    if actions:
        session._notify_sse("auto_use", {"trigger": trigger, "phase": "prepared", "actions": actions})
    return {"ok": True, "trigger": trigger, "actions": actions}


def run_pending_auto_use_actions(trigger: str = "") -> Dict[str, Any]:
    session = get_session()
    if not session.connected:
        return {"ok": False, "error": "未连接"}

    with session._lock:
        pending = [dict(x) for x in getattr(session, "auto_use_pending_actions", []) if isinstance(x, dict)]
        last_ts = dict(getattr(session, "auto_use_last_ts", {}))

    if not pending:
        return {"ok": True, "trigger": trigger, "actions": []}

    now = time.time()
    actions = []
    for pending_action in pending:
        item_id = str(pending_action.get("item_id", "")).strip().lower()
        res = use_auto_item(item_id)
        action_result = dict(pending_action)
        action_result.update(
            {
                "phase": "executed",
                "ok": bool(res.get("ok")),
                "error": res.get("error"),
            }
        )
        actions.append(action_result)
        if res.get("ok"):
            last_ts[pending_action["id"]] = now

    with session._lock:
        session.auto_use_last_ts = last_ts
        session.auto_use_pending_actions = []

    if actions:
        session._notify_sse("auto_use", {"trigger": trigger, "phase": "executed", "actions": actions})
    return {"ok": True, "trigger": trigger, "actions": actions}


def _decode_utf8_text(packet_hex: str) -> str:
    try:
        raw = bytes.fromhex(packet_hex[16:] if len(packet_hex) > 16 else packet_hex)
    except Exception:
        return ""
    text = raw.decode("utf-8", errors="ignore")
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text).strip()


def _parse_gold_to_copper(text: str):
    """
    将「获得金币」后的片段解析为总铜数。
    金:银:铜 = 1,000,000 铜 : 1,000 铜 : 1 铜。
    注意：不能用「金 in text」判断单位，否则「金币」会误判为带「金」单位。
    """
    m = re.search(r"(?:获得)?金币\s*[：:]\s*([^\r\n]+)", text)
    if not m:
        return None
    tail = m.group(1).strip()
    if not tail:
        return None

    jin = yin = tong = 0
    mj = re.search(r"(\d+)\s*金(?!币)", tail)
    my = re.search(r"(\d+)\s*银", tail)
    mt = re.search(r"(\d+)\s*铜", tail)
    if mj:
        jin = int(mj.group(1))
    if my:
        yin = int(my.group(1))
    if mt:
        tong = int(mt.group(1))
    if mj or my or mt:
        return jin * 1000 * 1000 + yin * 1000 + tong

    mp = re.fullmatch(r"(\d+)", tail)
    if mp:
        return int(mp.group(1))

    return None


def _build_battle_state_snapshot(session) -> Dict[str, Any]:
    return {
        "state": session.battle_state,
        "in_progress": session.battle_in_progress,
        "current_monster": session.battle_current_monster,
        "last_action": session.battle_last_action,
        "can_create_next": session.battle_can_create_next,
        "last_response_ts": session.battle_last_response_ts,
        "round_seq": session.battle_round_seq,
        "mode": session.battle_mode,
        "loop_running": session.battle_loop_running,
        "loop_monster_code": session.battle_loop_monster_code,
        "loop_delay_ms": session.battle_loop_delay_ms,
        "wait_deadline_ts": session.battle_wait_deadline_ts,
        "next_start_ts": session.battle_next_start_ts,
        "total_count": session.battle_total_count,
        "total_exp": session.battle_total_exp,
        "total_gold_copper": session.battle_total_gold_copper,
        "f703_timeout_recover_count": session.battle_f703_timeout_recover_count,
    }


def _set_battle_state(
    *,
    state: str | None = None,
    in_progress: bool | None = None,
    current_monster: str | None = None,
    last_action: str | None = None,
    can_create_next: bool | None = None,
    last_response_ts: float | None = None,
    round_seq: int | None = None,
    last_result: Dict[str, Any] | None = None,
    mode: str | None = None,
    wait_deadline_ts: float | None = None,
    next_start_ts: float | None = None,
) -> Dict[str, Any]:
    session = get_session()
    with session._lock:
        if state is not None:
            session.battle_state = state
        if in_progress is not None:
            session.battle_in_progress = in_progress
        if current_monster is not None:
            session.battle_current_monster = current_monster
        if last_action is not None:
            session.battle_last_action = last_action
        if can_create_next is not None:
            session.battle_can_create_next = can_create_next
        if last_response_ts is not None:
            session.battle_last_response_ts = last_response_ts
        if round_seq is not None:
            session.battle_round_seq = round_seq
        if last_result is not None:
            session.battle_last_result = dict(last_result)
        if mode is not None:
            session.battle_mode = mode
        if wait_deadline_ts is not None:
            session.battle_wait_deadline_ts = wait_deadline_ts
        if next_start_ts is not None:
            session.battle_next_start_ts = next_start_ts
        snapshot = _build_battle_state_snapshot(session)
    session.notify_battle_state()
    return snapshot


def _emit_battle_state_with_payload(event_type: str, payload: Dict[str, Any]) -> None:
    session = get_session()
    with session._lock:
        payload["battle_state"] = _build_battle_state_snapshot(session)
    session._notify_sse(event_type, payload)


def get_battle_state_snapshot() -> Dict[str, Any]:
    session = get_session()
    with session._lock:
        return _build_battle_state_snapshot(session)


def _emit_control_log(message: str, *, level: str = "info", scope: str = "battle", **extra) -> None:
    session = get_session()
    payload = {"scope": scope, "level": level, "message": message}
    if extra:
        payload.update(extra)
    session._notify_sse("control_log", payload)


def _set_loop_config(*, enabled: bool, monster_code: str | None = None, delay_ms: int | None = None) -> Dict[str, Any]:
    session = get_session()
    with session._lock:
        session.battle_loop_running = enabled
        if monster_code is not None:
            session.battle_loop_monster_code = monster_code
        if delay_ms is not None:
            session.battle_loop_delay_ms = max(0, int(delay_ms))
        if not enabled:
            session.battle_next_start_ts = 0.0
            if session.battle_mode == "loop":
                session.battle_mode = "single" if session.battle_in_progress else "idle"
        elif session.battle_mode != "loop":
            session.battle_mode = "loop"
        snapshot = _build_battle_state_snapshot(session)
    session.notify_battle_state()
    return snapshot


def _reset_battle_totals() -> None:
    session = get_session()
    with session._lock:
        session.battle_total_count = 0
        session.battle_total_exp = 0
        session.battle_total_gold_copper = 0
    session.notify_battle_state()


def _schedule_next_battle_round(delay_ms: int | None = None) -> Dict[str, Any]:
    session = get_session()
    now = time.time()
    with session._lock:
        if delay_ms is None:
            delay_ms = session.battle_loop_delay_ms
        session.battle_next_start_ts = now + max(0, int(delay_ms)) / 1000.0
        snapshot = _build_battle_state_snapshot(session)
    session.notify_battle_state()
    return snapshot


def clear_battle_wait_deadline() -> Dict[str, Any]:
    return _set_battle_state(wait_deadline_ts=0.0)


def _start_battle_round(monster_code: str, *, run_pre_battle_actions: bool) -> Dict[str, Any]:
    from services.action_manager import send_raw_action

    ok, err = can_start_battle()
    if not ok:
        return {"ok": False, "error": err, "battle_state": get_battle_state_snapshot()}
    try:
        built = build_start_battle_packet(monster_code)
    except ValueError as e:
        return {"ok": False, "error": str(e), "battle_state": get_battle_state_snapshot()}

    preflight_actions = []
    if run_pre_battle_actions:
        preflight_res = run_pre_battle_actions_fn()
        if not preflight_res.get("ok"):
            return {
                "ok": False,
                "error": preflight_res.get("error", "战斗前置动作失败"),
                "battle_state": get_battle_state_snapshot(),
            }
        preflight_actions = preflight_res.get("actions", [])

    res = send_raw_action(built["packet_hex"], priority=10, use_queue=True)
    if not res.get("ok"):
        return res

    mark_battle_started(built["monster_code"])
    result = {
        "ok": True,
        "queued": 1,
        **built,
        "battle_state": get_battle_state_snapshot(),
        "preflight_actions": preflight_actions,
    }
    if res.get("validation_warning"):
        result["validation_warning"] = res["validation_warning"]
    return result


def start_single_battle(monster_code: str, *, run_pre_battle_actions: bool = False) -> Dict[str, Any]:
    ok, err = can_start_battle()
    if not ok:
        return {"ok": False, "error": err, "battle_state": get_battle_state_snapshot()}
    session = get_session()
    with session._lock:
        session.battle_mode = "single"
        session.battle_loop_running = False
        session.battle_loop_monster_code = ""
        session.battle_next_start_ts = 0.0
    session.notify_battle_state()
    return _start_battle_round(monster_code, run_pre_battle_actions=run_pre_battle_actions)


def start_battle_loop(monster_code: str, *, loop_delay_ms: int) -> Dict[str, Any]:
    ok, err = can_start_battle()
    if not ok:
        return {"ok": False, "error": err, "battle_state": get_battle_state_snapshot()}
    session = get_session()
    monster = _normalize_hex4(monster_code, "monster_code")
    delay = max(0, int(loop_delay_ms))
    _reset_battle_totals()
    with session._lock:
        session.battle_mode = "loop"
        session.battle_loop_running = True
        session.battle_loop_monster_code = monster
        session.battle_loop_delay_ms = delay
        session.battle_next_start_ts = 0.0
    session.notify_battle_state()
    _emit_control_log(f"后端已接管循环战斗，目标怪物 {monster}，间隔 {delay}ms", scope="battle")
    result = _start_battle_round(monster, run_pre_battle_actions=not session.battle_preflight_teleport_used_once)
    if not result.get("ok"):
        _set_loop_config(enabled=False)
    return result


def stop_battle_loop(reason: str = "") -> Dict[str, Any]:
    from services.flow_manager import cancel_pending_reconnect

    snapshot = _set_loop_config(enabled=False)
    cancel_pending_reconnect("循环战斗已停止，取消后端自动重连")
    if reason:
        _emit_control_log(reason, level="info", scope="battle")
    return {"ok": True, "battle_state": snapshot}


def schedule_loop_restart_after_reconnect(delay_s: float = 0.3) -> Dict[str, Any]:
    return _set_battle_state(next_start_ts=time.time() + max(0.0, delay_s))


def start_loop_battle_round(monster_code: str, *, run_pre_battle_actions: bool = False) -> Dict[str, Any]:
    session = get_session()
    with session._lock:
        session.battle_mode = "loop"
        session.battle_loop_running = True
        session.battle_loop_monster_code = _normalize_hex4(
            monster_code or session.battle_loop_monster_code or session.battle_current_monster,
            "monster_code",
        )
        session.battle_next_start_ts = 0.0
    session.notify_battle_state()
    return _start_battle_round(session.battle_loop_monster_code, run_pre_battle_actions=run_pre_battle_actions)


def get_wait_timeout_reason() -> str:
    session = get_session()
    with session._lock:
        if session.battle_state == BATTLE_STATE_WAITING_START_RESPONSE:
            return "等待 f603 响应超时"
        if session.battle_state == BATTLE_STATE_WAITING_ACTION_RESULT:
            return "等待 f703 响应超时"
        return "战斗超时"


def is_battle_wait_timed_out(now: float | None = None) -> bool:
    session = get_session()
    if now is None:
        now = time.time()
    with session._lock:
        if session.battle_state not in (BATTLE_STATE_WAITING_START_RESPONSE, BATTLE_STATE_WAITING_ACTION_RESULT):
            return False
        wait_deadline_ts = float(session.battle_wait_deadline_ts or 0.0)
        if wait_deadline_ts <= 0:
            return False
        if now < wait_deadline_ts + BATTLE_WAIT_TIMEOUT_GRACE_S:
            return False
        last_response_ts = float(session.battle_last_response_ts or 0.0)
        if last_response_ts > 0 and last_response_ts >= wait_deadline_ts - BATTLE_WAIT_TIMEOUT_GRACE_S:
            return False
    return True


def run_pre_battle_actions_fn() -> Dict[str, Any]:
    return run_pre_battle_actions()


def build_start_battle_packet(monster_code: str) -> Dict[str, Any]:
    """构造发起战斗报文。"""
    monster = _normalize_hex4(monster_code, "monster_code")
    random_num = random_num_hex4()
    return {
        "packet_hex": _BATTLE_START_TEMPLATE.format(seq=random_num, monster=monster),
        "monster_code": monster,
        "random_num": random_num,
    }


def build_do_battle_packet() -> Dict[str, Any]:
    """构造进行战斗报文。"""
    random_num = random_num_hex4()
    session = get_session()
    with session._lock:
        job = (session.current_role.role_job if session.current_role else "") or ""
    job = str(job).strip()
    if job == "侠客":
        skill_job_hex = "03"
    elif job == "刺客":
        skill_job_hex = "66"
    else:
        skill_job_hex = "cb"
    return {
        "packet_hex": _BATTLE_SKILL_PACKET_TEMPLATE.format(random_num=random_num, skill_job_hex=skill_job_hex),
        "random_num": random_num,
    }


def can_start_battle() -> tuple[bool, str]:
    session = get_session()
    with session._lock:
        if session.battle_state in (BATTLE_STATE_WAITING_START_RESPONSE, BATTLE_STATE_WAITING_ACTION_RESULT):
            return False, f"战斗仍在进行中，当前状态={session.battle_state}"
    return True, ""


def can_manual_battle_do() -> tuple[bool, str]:
    session = get_session()
    with session._lock:
        if session.battle_state != BATTLE_STATE_WAITING_ACTION_RESULT or not session.battle_can_create_next:
            return False, f"当前状态不允许发送，state={session.battle_state}"
    return True, ""


def _build_teleport_ticket_packets(item_id: str) -> List[str]:
    item_hex = str(item_id or "").strip().lower()
    if len(item_hex) != 12:
        raise ValueError("传送券物品代码必须是 12 位 hex")
    return [
        item_use.build_use_item_packets(item_hex, 1)[0],
        _TELEPORT_TICKET_PACKET_TEMPLATE.format(random_1=random_num_hex4(), random_2=random_num_hex4()),
    ]


def _get_enabled_rule(rid: str) -> Dict[str, Any] | None:
    for rule in get_auto_use_rules():
        if rule.get("id") == rid and rule.get("enabled"):
            return rule
    return None


def run_pre_battle_actions() -> Dict[str, Any]:
    from services.action_manager import send_raw_action

    session = get_session()
    auto_use_res = run_pending_auto_use_actions("battle_start_preflight")
    if not auto_use_res.get("ok"):
        return auto_use_res
    actions = list(auto_use_res.get("actions", []))

    rule = _get_enabled_rule("battle_teleport_ticket")
    if not rule:
        return {"ok": True, "actions": actions}
    with session._lock:
        if session.battle_preflight_teleport_used_once:
            return {"ok": True, "actions": actions}

    item_id = str(rule.get("item_id", "")).strip().lower()
    if not item_id:
        return {"ok": False, "error": "已启用传送券规则，但 item_id 为空"}

    ok, err = item_use.optimistic_consume_item(item_id, 1)
    if not ok:
        return {"ok": False, "error": err}

    for idx, packet_hex in enumerate(_build_teleport_ticket_packets(item_id), start=1):
        res = send_raw_action(packet_hex, priority=10, use_queue=False)
        if not res.get("ok"):
            return res
        actions.append({
            "id": "battle_teleport_ticket",
            "ok": True,
            "item_id": item_id,
            "item_name": rule.get("item_name", "传送券"),
            "step": idx,
            "validation_warning": res.get("validation_warning", ""),
        })

    with session._lock:
        session.battle_preflight_teleport_used_once = True
    session.notify_backpack_update()
    session._notify_sse("auto_use", {"trigger": "battle_start_preflight", "actions": actions})
    return {"ok": True, "actions": actions}


def mark_battle_started(monster_code: str):
    session = get_session()
    wait_deadline = time.time() + DE07_TIMEOUT_S
    with session._lock:
        session.role_stats_full_refresh_on_next_ed07 = False
        session.battle_f703_timeout_recover_count = 0
        next_round = session.battle_round_seq + 1
        if not session.battle_current_monster:
            session.battle_current_monster = monster_code
    _set_battle_state(
        state=BATTLE_STATE_WAITING_START_RESPONSE,
        in_progress=True,
        current_monster=monster_code,
        last_action="f603",
        can_create_next=False,
        round_seq=next_round,
        last_result={},
        mode="loop" if session.battle_loop_running else session.battle_mode,
        wait_deadline_ts=wait_deadline,
        next_start_ts=0.0,
    )


def mark_battle_error(reason: str):
    session = get_session()
    with session._lock:
        session.battle_f703_timeout_recover_count = 0
    _set_battle_state(
        state=BATTLE_STATE_ERROR,
        in_progress=False,
        last_action="",
        can_create_next=False,
        last_result={"error": reason},
        wait_deadline_ts=0.0,
        next_start_ts=0.0,
    )


def reset_battle_state(*, preserve_loop: bool = False):
    session = get_session()
    with session._lock:
        loop_running = session.battle_loop_running if preserve_loop else False
        loop_monster_code = session.battle_loop_monster_code if preserve_loop else ""
        loop_delay_ms = session.battle_loop_delay_ms if preserve_loop else DEFAULT_BATTLE_LOOP_DELAY_MS
        battle_mode = session.battle_mode if preserve_loop and loop_running else "idle"
        total_count = session.battle_total_count if preserve_loop else 0
        total_exp = session.battle_total_exp if preserve_loop else 0
        total_gold = session.battle_total_gold_copper if preserve_loop else 0
        session.battle_loop_running = loop_running
        session.battle_loop_monster_code = loop_monster_code
        session.battle_loop_delay_ms = loop_delay_ms
        session.battle_mode = battle_mode
        session.battle_total_count = total_count
        session.battle_total_exp = total_exp
        session.battle_total_gold_copper = total_gold
        session.battle_next_start_ts = 0.0
        session.battle_f703_timeout_recover_count = 0
    _set_battle_state(
        state=BATTLE_STATE_IDLE,
        in_progress=False,
        current_monster=loop_monster_code if preserve_loop else "",
        last_action="",
        can_create_next=False,
        last_response_ts=0.0,
        round_seq=0,
        last_result={},
        mode=battle_mode,
        wait_deadline_ts=0.0,
        next_start_ts=0.0,
    )


def _send_next_f703(source: str) -> Dict[str, Any]:
    from services.action_manager import send_raw_action

    session = get_session()
    with session._lock:
        current_state = session.battle_state
        current_monster = session.battle_current_monster
        allowed = session.battle_can_create_next and current_state == BATTLE_STATE_WAITING_ACTION_RESULT
    if not allowed:
        return {"ok": False, "error": f"当前状态不允许自动发送 f703，state={current_state}", "source": source}

    built = build_do_battle_packet()
    res = send_raw_action(built["packet_hex"], priority=10, use_queue=True)
    if not res.get("ok"):
        mark_battle_error(res.get("error", "发送 f703 失败"))
        return res

    _set_battle_state(
        state=BATTLE_STATE_WAITING_ACTION_RESULT,
        in_progress=True,
        current_monster=current_monster,
        last_action="f703",
        can_create_next=False,
        last_result={"source": source, "sent": "f703", "random_num": built["random_num"]},
        wait_deadline_ts=time.time() + F703_TIMEOUT_S,
        next_start_ts=0.0,
    )
    return {"ok": True, **built, "source": source}


def recover_battle_wait_timeout_with_f703() -> Dict[str, Any]:
    """战斗等待超时后补发 f703；最多 MAX_F703_TIMEOUT_RECOVER 次，满次则 mark_battle_error。"""
    from services.action_manager import send_raw_action

    session = get_session()
    with session._lock:
        current_state = session.battle_state
        current_monster = session.battle_current_monster
        recover_count = session.battle_f703_timeout_recover_count

    if current_state not in (BATTLE_STATE_WAITING_START_RESPONSE, BATTLE_STATE_WAITING_ACTION_RESULT):
        return {"ok": False, "error": f"当前状态不允许超时恢复发送 f703，state={current_state}"}

    if recover_count >= MAX_F703_TIMEOUT_RECOVER:
        mark_battle_error(
            f"已达 {MAX_F703_TIMEOUT_RECOVER} 次 f703 超时重试，仍未收到含 {BATTLE_DE07_HEX_MARK} 的 de07"
        )
        return {"ok": False, "error": "f703 超时重试已达上限"}

    built = build_do_battle_packet()
    res = send_raw_action(built["packet_hex"], priority=10, use_queue=True)
    if not res.get("ok"):
        mark_battle_error(res.get("error", "发送 f703 失败"))
        return res

    with session._lock:
        session.battle_f703_timeout_recover_count = recover_count + 1
        new_count = session.battle_f703_timeout_recover_count

    _set_battle_state(
        state=BATTLE_STATE_WAITING_ACTION_RESULT,
        in_progress=True,
        current_monster=current_monster,
        last_action="f703",
        can_create_next=False,
        last_result={
            "source": "wait_timeout_recover",
            "sent": "f703",
            "random_num": built["random_num"],
            "timeout_recover_index": new_count,
        },
        wait_deadline_ts=time.time() + F703_TIMEOUT_S,
        next_start_ts=0.0,
    )
    return {"ok": True, **built, "recover_count": new_count}


def _base_battle_payload(packet_hex: str) -> Dict[str, Any]:
    return {
        "fingerprint": packet_hex[8:20] if len(packet_hex) >= 20 else "",
        "raw_text": _decode_utf8_text(packet_hex),
    }


def parse_battle_response(packet_hex: str) -> Dict[str, Any]:
    """解析 de07：提取 UTF-8 文本，并给出是否继续战斗的结构化结果。"""
    session = get_session()
    packet_hex_l = packet_hex.lower()
    payload = _base_battle_payload(packet_hex)
    text = payload["raw_text"]

    has_reward = any(token in packet_hex_l for token in _BATTLE_END_HEX_TOKENS)
    no_energy = _NEILI_NOT_ENOUGH_HEX_TOKEN in packet_hex_l or "内力不足" in text
    battle_ended = _BATTLE_ENDED_HEX_TOKEN in packet_hex_l or "战斗已结束" in text
    exp_match = re.search(r"(?:获得)?经验[：:+\s]*([0-9]+)", text)
    gold = _parse_gold_to_copper(text)

    with session._lock:
        prev_state = session.battle_state
        prev_round = session.battle_round_seq

    payload.update(
        {
            "exp": int(exp_match.group(1)) if exp_match else None,
            "gold": gold,
            "has_reward": has_reward,
            "no_energy": no_energy,
            "battle_ended": battle_ended,
            # 战斗结束与胜负仅由 e207（及 df07 内力不足）决定；de07 只负责首包后拉起第一轮 f703
            "is_end_confirmed": False,
            "end_reason": "",
            "should_continue": prev_state == BATTLE_STATE_WAITING_START_RESPONSE,
            "packet_type": "de07",
        }
    )

    now = time.time()

    if prev_state == BATTLE_STATE_WAITING_START_RESPONSE:
        _set_battle_state(
            state=BATTLE_STATE_WAITING_ACTION_RESULT,
            in_progress=True,
            last_action="de07",
            can_create_next=True,
            last_response_ts=now,
            round_seq=prev_round,
            last_result=payload,
        )
    elif prev_state == BATTLE_STATE_WAITING_ACTION_RESULT:
        _set_battle_state(
            state=BATTLE_STATE_WAITING_ACTION_RESULT,
            in_progress=True,
            last_action="de07",
            can_create_next=True,
            last_response_ts=now,
            round_seq=prev_round,
            last_result=payload,
        )
    else:
        _set_battle_state(
            state=prev_state,
            in_progress=prev_state in (BATTLE_STATE_WAITING_START_RESPONSE, BATTLE_STATE_WAITING_ACTION_RESULT),
            last_action="de07",
            can_create_next=False,
            last_response_ts=now,
            round_seq=prev_round,
            last_result=payload,
        )

    _emit_battle_state_with_payload("battle_response", payload)
    return payload


def _mark_role_stats_full_refresh_on_next_ed07() -> None:
    """战斗正式结束后，下一条含 ed07 的下行将整表替换 role_stats（由 flow_manager 分发）。"""
    s = get_session()
    with s._lock:
        s.role_stats_full_refresh_on_next_ed07 = True


def parse_battle_end(packet_hex: str) -> Dict[str, Any]:
    """解析 df07：三类——进行中（继续 f703）、内力不足（停战）、战斗已结束（仅提示，胜负看 e207）。"""
    session = get_session()
    packet_hex_l = packet_hex.lower()
    payload = _base_battle_payload(packet_hex)
    text = payload["raw_text"]
    no_energy = _NEILI_NOT_ENOUGH_HEX_TOKEN in packet_hex_l or "内力不足" in text
    battle_ended_notice = _BATTLE_ENDED_HEX_TOKEN in packet_hex_l or "战斗已结束" in text
    battle_pending = _DF07_BODY_BATTLE_CONTINUES in packet_hex_l and not no_energy and not battle_ended_notice

    stats_updated = update_session_stats(packet_hex)
    if no_energy:
        _mark_role_stats_full_refresh_on_next_ed07()
        df07_kind = "inner_force_short"
        is_end_confirmed = True
        should_continue = False
        end_reason = "no_energy"
    elif battle_ended_notice:
        df07_kind = "battle_already_ended_notice"
        is_end_confirmed = False
        should_continue = False
        end_reason = "battle_already_ended_notice"
    elif battle_pending:
        df07_kind = "battle_pending"
        is_end_confirmed = False
        should_continue = True
        end_reason = ""
    else:
        df07_kind = "unknown"
        is_end_confirmed = False
        should_continue = False
        end_reason = ""

    payload.update(
        {
            "exp": None,
            "gold": None,
            "has_reward": False,
            "no_energy": no_energy,
            "stats_updated": stats_updated,
            "df07_kind": df07_kind,
            "is_end_confirmed": is_end_confirmed,
            "end_reason": end_reason,
            "should_continue": should_continue,
            "packet_type": "df07",
        }
    )

    with session._lock:
        prev_state = session.battle_state
        prev_round = session.battle_round_seq
    now = time.time()

    if no_energy:
        _set_battle_state(
            state=BATTLE_STATE_ENDED,
            in_progress=False,
            last_action="",
            can_create_next=False,
            last_response_ts=now,
            round_seq=prev_round,
            last_result=payload,
            wait_deadline_ts=0.0,
        )
        _emit_battle_state_with_payload("battle_end", payload)
    elif battle_ended_notice:
        _set_battle_state(
            state=BATTLE_STATE_WAITING_ACTION_RESULT if prev_state == BATTLE_STATE_WAITING_ACTION_RESULT else prev_state,
            in_progress=prev_state == BATTLE_STATE_WAITING_ACTION_RESULT,
            last_action="df07",
            can_create_next=False,
            last_response_ts=now,
            round_seq=prev_round,
            last_result=payload,
            wait_deadline_ts=0.0,
        )
        _emit_battle_state_with_payload("battle_not_killed", payload)
    else:
        can_next = bool(battle_pending and prev_state == BATTLE_STATE_WAITING_ACTION_RESULT)
        _set_battle_state(
            state=BATTLE_STATE_WAITING_ACTION_RESULT if prev_state == BATTLE_STATE_WAITING_ACTION_RESULT else prev_state,
            in_progress=True,
            last_action="df07",
            can_create_next=can_next,
            last_response_ts=now,
            round_seq=prev_round,
            last_result=payload,
            wait_deadline_ts=0.0,
        )
        _emit_battle_state_with_payload("battle_not_killed", payload)
    return payload


def handle_battle_settlement_e207(packet_hex: str) -> Dict[str, Any]:
    """e8030100e207：按正文「失去」「获得」判定败/胜；仅胜场累计 battle_total_count 与经验金币。"""
    session = get_session()
    text = _decode_utf8_text(packet_hex)
    exp_match = re.search(r"(?:获得)?经验[：:+\s]*([0-9]+)", text)
    exp = int(exp_match.group(1)) if exp_match else None
    gold = _parse_gold_to_copper(text)

    with session._lock:
        prev_state = session.battle_state
        prev_round = session.battle_round_seq

    if prev_state not in (BATTLE_STATE_WAITING_ACTION_RESULT, BATTLE_STATE_WAITING_START_RESPONSE):
        return {
            "fingerprint": packet_hex[8:20] if len(packet_hex) >= 20 else "",
            "raw_text": text,
            "exp": exp,
            "gold": gold,
            "outcome": "ignored",
            "has_reward": False,
            "packet_type": "e207",
            "is_end_confirmed": False,
            "should_continue": False,
            "end_reason": "",
        }

    if "失去" in text:
        outcome = "defeat"
    elif "获得" in text:
        outcome = "victory"
    else:
        mark_battle_error("e207 结算包缺少「获得」或「失去」，无法判定胜负")
        return {
            "fingerprint": packet_hex[8:20] if len(packet_hex) >= 20 else "",
            "raw_text": text,
            "exp": exp,
            "gold": gold,
            "outcome": "unknown",
            "has_reward": False,
            "packet_type": "e207",
            "is_end_confirmed": False,
            "should_continue": False,
            "end_reason": "e207_parse_error",
        }

    _mark_role_stats_full_refresh_on_next_ed07()
    update_session_stats(packet_hex)
    now = time.time()
    with session._lock:
        session.battle_f703_timeout_recover_count = 0
        if outcome == "victory":
            session.battle_total_count += 1
            if isinstance(exp, int):
                session.battle_total_exp += exp
            if isinstance(gold, int):
                session.battle_total_gold_copper += gold

    payload: Dict[str, Any] = {
        "fingerprint": packet_hex[8:20] if len(packet_hex) >= 20 else "",
        "raw_text": text,
        "exp": exp,
        "gold": gold,
        "outcome": outcome,
        "has_reward": outcome == "victory",
        "no_energy": False,
        "packet_type": "e207",
        "is_end_confirmed": True,
        "should_continue": False,
        "end_reason": "e207_settlement",
    }

    _set_battle_state(
        state=BATTLE_STATE_ENDED,
        in_progress=False,
        last_action="e207",
        can_create_next=False,
        last_response_ts=now,
        round_seq=prev_round,
        last_result=payload,
        wait_deadline_ts=0.0,
        next_start_ts=0.0,
    )
    _emit_battle_state_with_payload("battle_settlement_e207", payload)
    return payload


def handle_battle_server_packet(packet_hex: str) -> Dict[str, Any]:
    """统一处理战斗下行 e207 / de07 / df07，并在允许时自动推进 f703。"""
    fingerprint = packet_hex[8:20] if len(packet_hex) >= 20 else ""
    if "e207" in fingerprint:
        payload = handle_battle_settlement_e207(packet_hex)
        if not payload.get("is_end_confirmed"):
            return {"ok": True, "payload": payload}
    elif "de07" in fingerprint:
        if BATTLE_DE07_HEX_MARK in packet_hex.lower():
            session = get_session()
            with session._lock:
                session.battle_f703_timeout_recover_count = 0
        payload = parse_battle_response(packet_hex)
    elif "df07" in fingerprint:
        payload = parse_battle_end(packet_hex)
    else:
        return {"ok": False, "error": "非战斗报文"}

    prepared_auto_use = None
    if payload.get("is_end_confirmed"):
        prepared_auto_use = prepare_auto_use_actions(f"battle_end:{payload.get('packet_type', '')}")

    auto_sent = None
    state = get_battle_state_snapshot().get("state")
    if payload.get("should_continue") and state == BATTLE_STATE_WAITING_ACTION_RESULT:
        auto_sent = _send_next_f703(payload.get("packet_type", ""))
    elif payload.get("is_end_confirmed"):
        defeatish = (
            payload.get("outcome") == "defeat"
            or payload.get("end_reason") == "no_energy"
        )
        if defeatish:
            if payload.get("outcome") == "defeat":
                stop_battle_loop("战斗失败，已停止循环")
            else:
                stop_battle_loop("内力不足，已停止循环")
        session = get_session()
        with session._lock:
            loop_running = session.battle_loop_running
            battle_mode = session.battle_mode
            delay_ms = session.battle_loop_delay_ms
        if loop_running:
            _schedule_next_battle_round(delay_ms)
        elif battle_mode == "single":
            _set_battle_state(mode="idle", next_start_ts=0.0)
    result = {"ok": True, "payload": payload}
    if prepared_auto_use is not None:
        result["prepared_auto_use"] = prepared_auto_use
    if auto_sent is not None:
        result["auto_sent"] = auto_sent
    return result


def use_auto_item(item_id: str) -> Dict[str, Any]:
    from services.action_manager import send_action

    return send_action("item.use", {"item_id": item_id, "quantity": 1})
