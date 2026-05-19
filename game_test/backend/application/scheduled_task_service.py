"""Scheduled feature runner and per-instance schedule config."""

from __future__ import annotations

import threading
import time
from typing import Any

from game_test.backend.domain.combat.battle import (
    get_battle_state_snapshot,
    start_battle_loop,
    stop_battle_loop,
)
from game_test.backend.infrastructure.data_manager import (
    DEFAULT_SCHEDULED_TASKS_CONFIG,
    load_liaoguo_pairs,
    load_scheduled_tasks_config,
    save_scheduled_tasks_config,
)
from game_test.backend.runtime import get_session

from .flow_service import (
    get_liaoguo_status,
    get_transport_supply_status,
    get_world_boss_status,
    send_daily_checkin,
    start_liaoguo,
    start_transport_supply,
    start_world_boss,
)

_lock = threading.Lock()
_last_triggered: set[str] = set()
_active_tasks: dict[str, dict[str, Any]] = {}
_last_checked_minute = ""
_last_config_error_log_ts = 0.0


def _emit_schedule_log(message: str, *, level: str = "info") -> None:
    get_session()._notify_sse("control_log", {"scope": "scheduled_tasks", "level": level, "message": message})


def _default_config() -> dict[str, Any]:
    return {
        k: dict(v) for k, v in DEFAULT_SCHEDULED_TASKS_CONFIG.items()
    }


def _valid_time(value: str) -> bool:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        return False
    hh, mm = value[:2], value[3:]
    if not (hh.isdigit() and mm.isdigit()):
        return False
    return 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59


def _normalize_times(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("times 必须是数组")
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        t = str(item or "").strip()
        if not _valid_time(t):
            raise ValueError(f"非法时间：{t}")
        if t not in seen:
            seen.add(t)
            out.append(t)
    out.sort()
    return out


def _normalize_config(raw: dict[str, Any]) -> dict[str, Any]:
    cfg = _default_config()
    if not isinstance(raw, dict):
        return cfg

    for key in ("daily_checkin", "transport_supply", "world_boss"):
        src = raw.get(key) if isinstance(raw.get(key), dict) else {}
        cfg[key]["enabled"] = bool(src.get("enabled", False))
        cfg[key]["times"] = _normalize_times(src.get("times", []))
    transport_src = raw.get("transport_supply") if isinstance(raw.get("transport_supply"), dict) else {}
    cfg["transport_supply"]["auto_use_gold_ticket"] = bool(transport_src.get("auto_use_gold_ticket", False))
    daily_src = raw.get("daily_checkin") if isinstance(raw.get("daily_checkin"), dict) else {}
    cfg["daily_checkin"]["run_on_login"] = bool(daily_src.get("run_on_login", False))

    liaoguo_src = raw.get("liaoguo") if isinstance(raw.get("liaoguo"), dict) else {}
    cfg["liaoguo"]["enabled"] = bool(liaoguo_src.get("enabled", False))
    liaoguo_time = str(liaoguo_src.get("time") or "").strip()
    if liaoguo_time and not _valid_time(liaoguo_time):
        raise ValueError(f"非法辽国时间：{liaoguo_time}")
    cfg["liaoguo"]["time"] = liaoguo_time

    pair_ids = liaoguo_src.get("pair_ids", [])
    if not isinstance(pair_ids, list):
        raise ValueError("liaoguo.pair_ids 必须是数组")
    known_ids = {str(p.get("id") or p.get("taskName") or "").strip() for p in load_liaoguo_pairs()}
    clean_ids: list[str] = []
    seen_ids: set[str] = set()
    for item in pair_ids:
        pid = str(item or "").strip()
        if not pid:
            continue
        if pid not in known_ids:
            raise ValueError(f"未知辽国层级：{pid}")
        if pid not in seen_ids:
            seen_ids.add(pid)
            clean_ids.append(pid)
    cfg["liaoguo"]["pair_ids"] = clean_ids
    return cfg


def get_scheduled_tasks_config() -> dict[str, Any]:
    try:
        config = _normalize_config(load_scheduled_tasks_config())
    except ValueError:
        config = _default_config()
    return {"ok": True, "config": config}


def update_scheduled_tasks_config(body: dict[str, Any]) -> dict[str, Any]:
    try:
        config = _normalize_config(body)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    save_scheduled_tasks_config(config)
    _emit_schedule_log("定时运行配置已保存", level="ok")
    return {"ok": True, "config": config}


def get_scheduled_tasks_status() -> dict[str, Any]:
    with _lock:
        active = {k: dict(v) for k, v in _active_tasks.items()}
        triggered_count = len(_last_triggered)
    return {"ok": True, "active_tasks": active, "triggered_count": triggered_count}


def _capture_and_stop_loop(feature_label: str) -> dict[str, Any] | None:
    state = get_battle_state_snapshot()
    if not state.get("loop_running"):
        return None
    monster = str(state.get("loop_monster_code") or "").strip().lower()
    delay = int(state.get("loop_delay_ms") or 0)
    if not monster:
        return None
    stop_battle_loop(f"定时任务「{feature_label}」暂停循环战斗")
    _emit_schedule_log(f"定时任务「{feature_label}」已暂停循环战斗，稍后恢复 {monster}/{delay}ms", level="info")
    _wait_for_battle_idle(feature_label)
    return {"monster_code": monster, "loop_delay_ms": delay}


def _wait_for_battle_idle(feature_label: str, timeout_s: float = 180.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        state = get_battle_state_snapshot()
        if not state.get("in_progress"):
            return
        time.sleep(0.5)
    _emit_schedule_log(f"定时任务「{feature_label}」等待当前战斗结束超时，将继续执行", level="warn")


def _restore_loop(snapshot: dict[str, Any] | None, feature_label: str) -> None:
    if not snapshot:
        return
    session = get_session()
    if not session.connected or not session.sock:
        _emit_schedule_log(f"定时任务「{feature_label}」结束，但当前未连接，无法恢复循环战斗", level="warn")
        return
    res = start_battle_loop(str(snapshot.get("monster_code") or ""), loop_delay_ms=int(snapshot.get("loop_delay_ms") or 0))
    if res.get("ok"):
        _emit_schedule_log(f"定时任务「{feature_label}」结束，已恢复循环战斗", level="ok")
    else:
        _emit_schedule_log(f"定时任务「{feature_label}」结束，恢复循环战斗失败：{res.get('error', '未知错误')}", level="err")


def _set_active(feature: str, label: str, scheduled_time: str) -> bool:
    with _lock:
        if feature in _active_tasks:
            return False
        _active_tasks[feature] = {"label": label, "scheduled_time": scheduled_time, "started_at": time.time()}
        return True


def _clear_active(feature: str) -> None:
    with _lock:
        _active_tasks.pop(feature, None)


def _wait_until_not_running(feature: str) -> None:
    while True:
        if feature == "transport_supply" and not get_transport_supply_status().get("running"):
            return
        if feature == "world_boss" and not get_world_boss_status().get("running"):
            return
        if feature == "liaoguo" and not get_liaoguo_status().get("running"):
            return
        time.sleep(0.5)


def _run_daily_checkin(scheduled_time: str) -> None:
    feature = "daily_checkin"
    label = "每日签到"
    if not _set_active(feature, label, scheduled_time):
        _emit_schedule_log("每日签到定时已在执行，忽略本次触发", level="info")
        return
    loop_snapshot = _capture_and_stop_loop(label)
    try:
        res = send_daily_checkin(source="scheduled")
        if not res.get("ok"):
            _emit_schedule_log(f"每日签到定时发送失败：{res.get('error', '未知错误')}", level="err")
    finally:
        _restore_loop(loop_snapshot, label)
        _clear_active(feature)


def _run_waiting_feature(scheduled_time: str, feature: str, label: str, starter) -> None:
    if not _set_active(feature, label, scheduled_time):
        _emit_schedule_log(f"{label}定时已在执行，忽略本次触发", level="info")
        return
    loop_snapshot = _capture_and_stop_loop(label)
    try:
        res = starter()
        if not res.get("ok"):
            _emit_schedule_log(f"{label}定时启动失败：{res.get('error', '未知错误')}", level="err")
            return
        _emit_schedule_log(f"{label}定时已启动", level="ok")
        _wait_until_not_running(feature)
    finally:
        _restore_loop(loop_snapshot, label)
        _clear_active(feature)


def _run_liaoguo_sequence(scheduled_time: str, pair_ids: list[str]) -> None:
    feature = "liaoguo"
    label = "辽国战斗"
    if not _set_active(feature, label, scheduled_time):
        _emit_schedule_log("辽国战斗定时已在执行，忽略本次触发", level="info")
        return
    loop_snapshot = _capture_and_stop_loop(label)
    try:
        pairs = load_liaoguo_pairs()
        by_id = {str(p.get("id") or p.get("taskName") or "").strip(): p for p in pairs}
        for index, pair_id in enumerate(pair_ids, start=1):
            pair = by_id.get(pair_id)
            if not pair:
                _emit_schedule_log(f"辽国第 {index} 层级不存在，已跳过：{pair_id}", level="warn")
                continue
            res = start_liaoguo(pair)
            if not res.get("ok"):
                _emit_schedule_log(f"辽国第 {index} 层启动失败：{res.get('error', '未知错误')}", level="err")
                continue
            _emit_schedule_log(f"辽国第 {index}/{len(pair_ids)} 层已启动：{pair.get('label') or pair_id}", level="ok")
            _wait_until_not_running(feature)
            if get_liaoguo_status().get("stop_requested"):
                _emit_schedule_log("辽国战斗定时已被手动停止，结束后续层级", level="info")
                break
    finally:
        _restore_loop(loop_snapshot, label)
        _clear_active(feature)


def _feature_already_running(feature: str) -> bool:
    with _lock:
        if feature in _active_tasks:
            return True
    if feature == "transport_supply":
        return bool(get_transport_supply_status().get("running"))
    if feature == "world_boss":
        return bool(get_world_boss_status().get("running"))
    if feature == "liaoguo":
        return bool(get_liaoguo_status().get("running"))
    return False


def _launch(feature: str, scheduled_time: str, target, *args) -> None:
    if _feature_already_running(feature):
        _emit_schedule_log(f"{feature} 到点但功能正在运行，已忽略", level="info")
        return
    threading.Thread(target=target, args=(scheduled_time, *args), daemon=True, name=f"scheduled-{feature}").start()


def tick_scheduled_tasks(now: float) -> None:
    global _last_checked_minute, _last_config_error_log_ts
    local = time.localtime(now)
    minute_key = time.strftime("%Y-%m-%d %H:%M", local)
    with _lock:
        if _last_checked_minute == minute_key:
            return
        _last_checked_minute = minute_key
    try:
        config = _normalize_config(load_scheduled_tasks_config())
    except ValueError as e:
        if now - _last_config_error_log_ts > 60:
            _last_config_error_log_ts = now
            _emit_schedule_log(f"定时配置无效：{e}", level="err")
        return
    today = time.strftime("%Y-%m-%d", local)
    hhmm = time.strftime("%H:%M", local)

    def should_fire(feature: str, scheduled_time: str) -> bool:
        key = f"{today}|{feature}|{scheduled_time}"
        with _lock:
            if key in _last_triggered:
                return False
            _last_triggered.add(key)
            if len(_last_triggered) > 2048:
                for old in list(_last_triggered)[:1024]:
                    _last_triggered.discard(old)
        return True

    daily = config["daily_checkin"]
    if daily["enabled"] and hhmm in daily["times"] and should_fire("daily_checkin", hhmm):
        _launch("daily_checkin", hhmm, _run_daily_checkin)

    transport = config["transport_supply"]
    if transport["enabled"] and hhmm in transport["times"] and should_fire("transport_supply", hhmm):
        _launch(
            "transport_supply",
            hhmm,
            _run_waiting_feature,
            "transport_supply",
            "运输物资",
            lambda: start_transport_supply(auto_use_gold_ticket=bool(transport.get("auto_use_gold_ticket", False))),
        )

    world_boss = config["world_boss"]
    if world_boss["enabled"] and hhmm in world_boss["times"] and should_fire("world_boss", hhmm):
        _launch("world_boss", hhmm, _run_waiting_feature, "world_boss", "世界 BOSS", start_world_boss)

    liaoguo = config["liaoguo"]
    if (
        liaoguo["enabled"]
        and liaoguo["time"]
        and hhmm == liaoguo["time"]
        and liaoguo["pair_ids"]
        and should_fire("liaoguo", hhmm)
    ):
        _launch("liaoguo", hhmm, _run_liaoguo_sequence, list(liaoguo["pair_ids"]))


def maybe_run_login_checkin() -> None:
    try:
        config = _normalize_config(load_scheduled_tasks_config())
    except ValueError:
        return
    if not bool(config["daily_checkin"].get("run_on_login")):
        return
    res = send_daily_checkin(source="login")
    if not res.get("ok"):
        _emit_schedule_log(f"登录自动签到失败：{res.get('error', '未知错误')}", level="err")


__all__ = [
    "get_scheduled_tasks_config",
    "update_scheduled_tasks_config",
    "get_scheduled_tasks_status",
    "tick_scheduled_tasks",
    "maybe_run_login_checkin",
]
