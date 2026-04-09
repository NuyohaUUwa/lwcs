"""
战斗功能：
1) 客户端发起战斗（f603）
2) 客户端进行战斗（f703）
3) 解析服务器战斗响应（de07）
4) 解析服务器战斗结束（df07）
"""

import random
import re
import json
import os
import time
from typing import Dict, List, Any

from core.connector import enqueue_packet
from core.session import get_session
from features.role_stats import update_session_stats
from features.packet_probe import record_packet
from features.item_use import use_item

# 发起战斗模板：{seq} 为 4 位 hex，{monster} 为 4 位怪物代码
_BATTLE_START_TEMPLATE = "1b000000e8030500f603{seq}f505fc030000090000000100{monster}0000000000"

# 进行战斗（群体技能）包
_BATTLE_SKILL_PACKET_TEMPLATE = (
    "22000000e8030500f703{random_num}f50504040000100000000100cb00000000000000030001020000"
)

def _load_default_monsters() -> list:
    """从 data/monsters.json 加载默认怪物列表。"""
    data_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "monsters.json")
    try:
        with open(data_file, "r", encoding="utf-8") as f:
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
    "e88eb7e5be97e98791e5b8813a20",    # 获得金币: (半角冒号+空格)
)
_NEILI_NOT_ENOUGH_HEX_TOKEN = "e58685e58a9be4b88de8b6b3"  # 内力不足
AUTO_USE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "auto_use_rules.json")


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


def _load_auto_use_rules() -> List[Dict[str, Any]]:
    try:
        with open(AUTO_USE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, list):
            raise ValueError("rules must be list")
    except Exception:
        return []

    out: List[Dict[str, Any]] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("id", "")).strip()
        if not rid:
            continue
        out.append(_normalize_auto_use_rule(r))
    return out


def _save_auto_use_rules(rules: List[Dict[str, Any]]) -> None:
    with open(AUTO_USE_FILE, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)


def get_auto_use_rules() -> List[Dict[str, Any]]:
    session = get_session()
    with session._lock:
        rules = getattr(session, "auto_use_rules", None)
        if rules is None:
            rules = _load_auto_use_rules()
            session.auto_use_rules = rules
            session.auto_use_last_ts = {}
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
    session = get_session()
    if not session.connected:
        return {"ok": False, "error": "未连接"}

    with session._lock:
        rules = [dict(x) for x in getattr(session, "auto_use_rules", _load_auto_use_rules())]
        session.auto_use_rules = rules
        stats = dict(session.role_stats)
        items = {k: v.to_dict() for k, v in session.backpack_items.items()}
        last_ts = dict(getattr(session, "auto_use_last_ts", {}))

    now = time.time()
    actions = []
    for rule in rules:
        if not rule.get("enabled"):
            continue
        item_id = str(rule.get("item_id", "")).strip().lower()
        if not item_id:
            continue
        stat_key = rule.get("stat_key", "")
        threshold = int(rule.get("threshold", 0))
        stat_val = _to_int(stats.get(stat_key, 0))
        if stat_val >= threshold:
            continue
        item = items.get(item_id)
        if not item or int(item.get("quantity", 0)) <= 0:
            actions.append({"id": rule["id"], "ok": False, "reason": "背包无可用物品"})
            continue
        if now - float(last_ts.get(rule["id"], 0.0)) < 3.0:
            continue

        res = use_item(item_id, 1)
        actions.append({
            "id": rule["id"],
            "ok": bool(res.get("ok")),
            "item_id": item_id,
            "item_name": rule.get("item_name", ""),
            "stat_key": stat_key,
            "stat_value": stat_val,
            "threshold": threshold,
            "error": res.get("error"),
        })
        if res.get("ok"):
            last_ts[rule["id"]] = now

    with session._lock:
        session.auto_use_last_ts = last_ts

    if actions:
        session._notify_sse("auto_use", {"trigger": trigger, "actions": actions})
    return {"ok": True, "trigger": trigger, "actions": actions}


def _random_num_hex4() -> str:
    """等价 Kotlin: Random.nextInt(0x0000, 0xFFFF).toString(16).padStart(4, '0')"""
    return format(random.randint(0x0000, 0xFFFF), "04x")


def _decode_utf8_text(packet_hex: str) -> str:
    try:
        raw = bytes.fromhex(packet_hex[16:] if len(packet_hex) > 16 else packet_hex)
    except Exception:
        return ""
    text = raw.decode("utf-8", errors="ignore")
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text).strip()


def _parse_gold_to_copper(text: str):
    """
    将“获得金币: 1金2银3铜 / 17铜 / 5银”等转换为铜单位总数。
    金:银:铜 = 1000:1000:1
    """
    m = re.search(r"(?:获得)?金币[：:]\s*([0-9]+)?(?:金)?\s*([0-9]+)?(?:银)?\s*([0-9]+)?(?:铜)?", text)
    if not m:
        return None

    # 若原文明确出现单位，按单位组合；否则不认
    has_jin = "金" in text
    has_yin = "银" in text
    has_tong = "铜" in text
    if not (has_jin or has_yin or has_tong):
        return None

    a, b, c = m.group(1), m.group(2), m.group(3)
    # 为兼容“17铜”这类只含一个数字的场景：
    # 先把所有数字抓出来，再按出现的单位映射
    nums = [int(x) for x in re.findall(r"([0-9]+)", m.group(0))]
    if not nums:
        return None

    jin = yin = tong = 0
    if has_jin and has_yin and has_tong and len(nums) >= 3:
        jin, yin, tong = nums[0], nums[1], nums[2]
    elif has_jin and has_yin and len(nums) >= 2:
        jin, yin = nums[0], nums[1]
    elif has_jin and has_tong and len(nums) >= 2:
        jin, tong = nums[0], nums[1]
    elif has_yin and has_tong and len(nums) >= 2:
        yin, tong = nums[0], nums[1]
    elif has_jin:
        jin = nums[0]
    elif has_yin:
        yin = nums[0]
    else:
        tong = nums[0]

    return jin * 1000 * 1000 + yin * 1000 + tong


def start_battle(monster_code: str) -> Dict:
    """发起战斗：构造 f603 报文并入发送队列。"""
    session = get_session()
    if not session.connected:
        return {"ok": False, "error": "未连接游戏服"}
    try:
        monster = _normalize_hex4(monster_code, "monster_code")
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    random_num = _random_num_hex4()
    packet_hex = _BATTLE_START_TEMPLATE.format(seq=random_num, monster=monster)
    enqueue_packet(session.send_queue, packet_hex, priority=10)
    record_packet(packet_hex, "UP")
    # 标记进入战斗流程（用于“未秒杀包”触发重发）
    with session._lock:
        session.battle_in_progress = True
        session.battle_last_retry_ts = 0.0
    return {
        "ok": True,
        "queued": 1,
        "monster_code": monster,
        "random_num": random_num,
        "packet_hex": packet_hex,
    }


def do_battle() -> Dict:
    """进行战斗：发送群体技能包 f703（包含随机 random_num）。"""
    session = get_session()
    if not session.connected:
        return {"ok": False, "error": "未连接游戏服"}
    random_num = _random_num_hex4()
    packet_hex = _BATTLE_SKILL_PACKET_TEMPLATE.format(random_num=random_num)
    enqueue_packet(session.send_queue, packet_hex, priority=10)
    record_packet(packet_hex, "UP")
    return {"ok": True, "queued": 1, "packet_hex": packet_hex, "random_num": random_num}


def one_shot_kill(monster_code: str) -> Dict:
    """秒杀流程：先发起战斗，再立即发送战斗技能。"""
    start = start_battle(monster_code)
    if not start.get("ok"):
        return start
    fight = do_battle()
    if not fight.get("ok"):
        return fight
    return {
        "ok": True,
        "queued": 2,
        "monster_code": start.get("monster_code"),
        "random_num": start.get("random_num"),
    }


def parse_battle_response(packet_hex: str) -> Dict:
    """解析 de07：提取 UTF-8 文本并广播战斗过程。"""
    session = get_session()
    text = _decode_utf8_text(packet_hex)
    payload = {
        "fingerprint": packet_hex[8:20] if len(packet_hex) >= 20 else "",
        "raw_text": text,
    }
    session._notify_sse("battle_response", payload)
    return payload


def parse_battle_end(packet_hex: str) -> Dict:
    """解析 df07：含经验/金币则战斗结束，否则判定未秒杀。"""
    session = get_session()
    packet_hex_l = packet_hex.lower()
    text = _decode_utf8_text(packet_hex)
    exp_match = re.search(r"(?:获得)?经验[：:+\s]*([0-9]+)", text)
    exp = int(exp_match.group(1)) if exp_match else None
    gold = _parse_gold_to_copper(text)

    # 简化判定：命中指定 hex 片段即视为战斗结束
    has_reward = any(token in packet_hex_l for token in _BATTLE_END_HEX_TOKENS)
    no_energy = _NEILI_NOT_ENOUGH_HEX_TOKEN in packet_hex_l

    # 仅在明确结算时结束战斗流程；否则保持进行中，等待前端继续发 f703
    if has_reward or no_energy:
        with session._lock:
            session.battle_in_progress = False

    stats_updated = update_session_stats(packet_hex)
    payload = {
        "fingerprint": packet_hex[8:20] if len(packet_hex) >= 20 else "",
        "raw_text": text,
        "exp": exp,
        "gold": gold,
        "has_reward": has_reward,
        "no_energy": no_energy,
        "stats_updated": stats_updated,
    }
    if has_reward:
        session._notify_sse("battle_end", payload)
    else:
        session._notify_sse("battle_not_killed", payload)
    return payload
