"""HTTP-facing ladder façade."""

import re
import threading
import time
from typing import Any

from game_test.backend.domain.ladder.ladder import build_ladder_challenge_packet, build_team_invite_packet
from game_test.backend.domain.ladder.small_runtime import small_account_manager
from game_test.backend.domain.combat.battle import can_start_battle, mark_battle_started
from game_test.backend.infrastructure.data_manager import (
    delete_small_account,
    get_small_accounts,
    move_small_account,
    upsert_small_account,
)
from game_test.backend.runtime import get_session
from game_test.backend.runtime.actions import send_raw_action

_LADDER_WAIT_TIMEOUT_S = 180.0


class _LadderAutoState:
    def __init__(self) -> None:
        self.running = False
        self.start_floor = 1
        self.end_floor = 1
        self.current_floor = 0
        self.completed_floor = 0
        self.last_error = ""
        self.stop_event = threading.Event()
        self.lock = threading.Lock()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "ok": True,
                "running": self.running,
                "start_floor": self.start_floor,
                "end_floor": self.end_floor,
                "current_floor": self.current_floor,
                "completed_floor": self.completed_floor,
                "last_error": self.last_error,
            }


_ladder_auto_state = _LadderAutoState()
_ladder_auto_thread: threading.Thread | None = None


def _current_main_account() -> str:
    session = get_session()
    return str(session.account or "").strip()


def _emit_ladder_event(event: str, message: str, **extra: Any) -> None:
    payload = {"event": event, "message": message}
    payload.update(extra)
    get_session()._notify_sse("ladder_auto", payload)


def challenge_ladder(payload: dict[str, Any]) -> dict[str, Any]:
    ok, err = can_start_battle()
    if not ok:
        return {"ok": False, "error": err}
    try:
        built = build_ladder_challenge_packet(int(payload.get("floor", 0)))
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    res = send_raw_action(str(built["packet_hex"]), priority=10, use_queue=True)
    if not res.get("ok"):
        return res
    session = get_session()
    with session._lock:
        session.battle_loop_running = False
        session.battle_loop_monster_code = ""
        session.battle_next_start_ts = 0.0
    mark_battle_started(str(built["target_id_le"]))
    return {"ok": True, "queued": 1, "battle_state": session.get_status().get("battle_state"), **built}


def _wait_current_ladder_battle_done(floor: int) -> dict[str, Any]:
    deadline = time.time() + _LADDER_WAIT_TIMEOUT_S
    while time.time() < deadline:
        if _ladder_auto_state.stop_event.wait(0.3):
            return {"ok": False, "stopped": True, "error": "已停止"}
        session = get_session()
        with session._lock:
            state = str(session.battle_state or "")
            last_result = dict(session.battle_last_result or {})
        if state == "error":
            return {"ok": False, "error": str(last_result.get("error") or "战斗异常")}
        if state == "idle":
            return {"ok": True}
    return {"ok": False, "error": f"第 {floor} 层等待战斗结束超时"}


def _run_ladder_auto(start_floor: int, end_floor: int) -> None:
    try:
        for floor in range(start_floor, end_floor + 1):
            if _ladder_auto_state.stop_event.is_set():
                break
            with _ladder_auto_state.lock:
                _ladder_auto_state.current_floor = floor
                _ladder_auto_state.last_error = ""
            _emit_ladder_event("floor_start", f"开始挑战第 {floor} 层", floor=floor)
            res = challenge_ladder({"floor": floor})
            if not res.get("ok"):
                err = str(res.get("error") or "启动失败")
                with _ladder_auto_state.lock:
                    _ladder_auto_state.last_error = err
                _emit_ladder_event("error", f"第 {floor} 层启动失败：{err}", floor=floor)
                return
            wait_res = _wait_current_ladder_battle_done(floor)
            if not wait_res.get("ok"):
                if wait_res.get("stopped"):
                    _emit_ladder_event("stopped", "一键挑战已停止")
                    return
                err = str(wait_res.get("error") or "战斗失败")
                with _ladder_auto_state.lock:
                    _ladder_auto_state.last_error = err
                _emit_ladder_event("error", err, floor=floor)
                return
            with _ladder_auto_state.lock:
                _ladder_auto_state.completed_floor = floor
            _emit_ladder_event("floor_done", f"第 {floor} 层完成", floor=floor)
            if floor < end_floor:
                _ladder_auto_state.stop_event.wait(1.0)
        _emit_ladder_event("finished", f"一键挑战完成：{start_floor}~{end_floor} 层")
    finally:
        with _ladder_auto_state.lock:
            _ladder_auto_state.running = False


def start_ladder_auto(payload: dict[str, Any]) -> dict[str, Any]:
    global _ladder_auto_thread
    with _ladder_auto_state.lock:
        if _ladder_auto_state.running:
            return {"ok": False, "error": "一键挑战正在运行"}
    try:
        start_floor = int(payload.get("start_floor", 1) or 1)
        end_floor = int(payload.get("end_floor", payload.get("to_floor", 1)) or 1)
    except (TypeError, ValueError):
        return {"ok": False, "error": "楼层必须是整数"}
    if start_floor < 1 or end_floor > 20 or start_floor > end_floor:
        return {"ok": False, "error": "楼层范围必须满足 1 <= 起始层 <= 结束层 <= 20"}
    ok, err = can_start_battle()
    if not ok:
        return {"ok": False, "error": err}

    with _ladder_auto_state.lock:
        _ladder_auto_state.running = True
        _ladder_auto_state.start_floor = start_floor
        _ladder_auto_state.end_floor = end_floor
        _ladder_auto_state.current_floor = 0
        _ladder_auto_state.completed_floor = 0
        _ladder_auto_state.last_error = ""
        _ladder_auto_state.stop_event.clear()
    _ladder_auto_thread = threading.Thread(
        target=_run_ladder_auto,
        args=(start_floor, end_floor),
        daemon=True,
        name="ladder-auto",
    )
    _ladder_auto_thread.start()
    _emit_ladder_event("started", f"一键挑战已启动：{start_floor}~{end_floor} 层", start_floor=start_floor, end_floor=end_floor)
    return _ladder_auto_state.snapshot()


def stop_ladder_auto() -> dict[str, Any]:
    _ladder_auto_state.stop_event.set()
    with _ladder_auto_state.lock:
        _ladder_auto_state.running = False
    _emit_ladder_event("stopped", "已请求停止一键挑战")
    return _ladder_auto_state.snapshot()


def get_ladder_auto_status() -> dict[str, Any]:
    return _ladder_auto_state.snapshot()


def invite_team(payload: dict[str, Any]) -> dict[str, Any]:
    target_ids = payload.get("target_user_ids", [])
    if isinstance(target_ids, str):
        target_ids = [x for x in re.split(r"[\s,，;；]+", target_ids) if x]
    elif not isinstance(target_ids, list):
        target_ids = []
    clean_ids = [str(x).strip().lower() for x in target_ids if str(x).strip()]
    if bool(payload.get("include_managed_online", False)):
        clean_ids.extend(small_account_manager.online_user_ids())
    clean_ids = list(dict.fromkeys(clean_ids))
    if not clean_ids:
        return {"ok": False, "error": "请提供要邀请的小号角色 ID"}

    invited = []
    for target_id in clean_ids:
        try:
            built = build_team_invite_packet(target_id)
        except ValueError as exc:
            return {"ok": False, "error": f"{target_id}: {exc}"}
        res = send_raw_action(built["packet_hex"], priority=10, use_queue=True)
        if not res.get("ok"):
            return res
        invited.append(built["target_user_id"])
    return {"ok": True, "queued": len(invited), "invited": invited}


def list_small_accounts() -> dict[str, Any]:
    return get_small_accounts(_current_main_account())


def save_small_account(payload: dict[str, Any]) -> dict[str, Any]:
    return upsert_small_account(_current_main_account(), payload)


def remove_small_account(account: str) -> dict[str, Any]:
    return delete_small_account(_current_main_account(), account)


def reorder_small_account(account: str, direction: str) -> dict[str, Any]:
    return move_small_account(_current_main_account(), account, direction)


def start_small_accounts() -> dict[str, Any]:
    account_res = list_small_accounts()
    if not account_res.get("ok"):
        return account_res
    return small_account_manager.start_first_two(list(account_res.get("items", [])))


def start_small_account(account: str) -> dict[str, Any]:
    account_res = list_small_accounts()
    if not account_res.get("ok"):
        return account_res
    items = list(account_res.get("items", []))
    target = next((item for item in items if str(item.get("account", "")).strip() == str(account or "").strip()), None)
    if not target:
        return {"ok": False, "error": "小号不存在"}
    return small_account_manager.start_one(target)


def stop_small_accounts() -> dict[str, Any]:
    return small_account_manager.stop_all()


def stop_small_account(account: str) -> dict[str, Any]:
    return small_account_manager.stop_one(account)


def get_small_status() -> dict[str, Any]:
    return {"ok": True, "items": small_account_manager.status()}
