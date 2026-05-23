"""HTTP-facing ladder façade."""

import re
import threading
import time
from typing import Any

from game_test.backend.domain.ladder.ladder import build_ladder_challenge_packet, build_team_invite_packet
from game_test.backend.domain.ladder.small_runtime import small_account_manager
from game_test.backend.domain.combat.battle import can_start_battle, mark_battle_started, reset_battle_state
from game_test.backend.infrastructure.data_manager import (
    delete_small_account,
    get_small_accounts,
    move_small_account,
    upsert_small_account,
)
from game_test.backend.runtime import get_session
from game_test.backend.runtime.actions import send_raw_action

_LADDER_WAIT_TIMEOUT_S = 180.0
_SMALL_ONLINE_WAIT_S = 90.0
_SMALL_JOIN_WAIT_S = 30.0
_MAIN_DISCONNECT_POLL_S = 0.5


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
        self.run_id = 0
        self.need_small_recovery = False
        self.main_disconnect_pending = False
        self.resume_floor = 0
        self.target_accounts: list[str] = []

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
                "main_disconnect_pending": self.main_disconnect_pending,
                "need_small_recovery": self.need_small_recovery,
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


def _is_current_run(run_id: int) -> bool:
    with _ladder_auto_state.lock:
        return _ladder_auto_state.running and _ladder_auto_state.run_id == run_id


def is_ladder_auto_running() -> bool:
    with _ladder_auto_state.lock:
        return bool(_ladder_auto_state.running)


def _snapshot_target_accounts() -> list[str]:
    return small_account_manager.snapshot_target_accounts()


def _refresh_target_accounts_locked() -> None:
    live = small_account_manager.snapshot_target_accounts()
    if live:
        _ladder_auto_state.target_accounts = live


def _set_run_error(run_id: int, error: str, *, floor: int | None = None) -> None:
    with _ladder_auto_state.lock:
        if _ladder_auto_state.run_id != run_id:
            return
        _ladder_auto_state.last_error = error
        _ladder_auto_state.running = False
        _ladder_auto_state.main_disconnect_pending = False
        _ladder_auto_state.stop_event.set()
    extra = {"floor": floor} if floor is not None else {}
    _emit_ladder_event("error", error, **extra)


def _consume_small_recovery_flag(run_id: int) -> bool:
    with _ladder_auto_state.lock:
        if not _ladder_auto_state.running or _ladder_auto_state.run_id != run_id:
            return False
        need = _ladder_auto_state.need_small_recovery
        _ladder_auto_state.need_small_recovery = False
        return need


def _recover_small_team(run_id: int, stop_event: threading.Event) -> dict[str, Any]:
    with _ladder_auto_state.lock:
        if not _ladder_auto_state.running or _ladder_auto_state.run_id != run_id:
            return {"ok": False, "stale": True, "error": "一键挑战已被新的运行替换"}
        targets = list(_ladder_auto_state.target_accounts)
    if not targets:
        return {"ok": True, "skipped": True}
    if stop_event.is_set():
        return {"ok": False, "stopped": True, "error": "已停止"}

    _emit_ladder_event("recover_start", f"正在恢复托管小号：{', '.join(targets)}")
    small_account_manager.stop_targets(targets)
    if stop_event.is_set():
        return {"ok": False, "stopped": True, "error": "已停止"}

    account_res = list_small_accounts()
    if not account_res.get("ok"):
        return account_res
    items = list(account_res.get("items", []))
    small_account_manager.reset_team_joined_for_targets(targets)
    start_res = small_account_manager.start_targets(targets, items, force=True)
    if not start_res.get("ok"):
        return start_res

    online_res = small_account_manager.wait_targets_online(targets, _SMALL_ONLINE_WAIT_S, stop_event=stop_event)
    if not online_res.get("ok"):
        return online_res
    if not _is_current_run(run_id):
        return {"ok": False, "stale": True, "error": "一键挑战已被新的运行替换"}

    invite_res = invite_team({"include_managed_online": True})
    if not invite_res.get("ok"):
        return invite_res

    if stop_event.wait(1.0):
        return {"ok": False, "stopped": True, "error": "已停止"}
    join_res = small_account_manager.wait_targets_joined(targets, _SMALL_JOIN_WAIT_S, stop_event=stop_event)
    if not join_res.get("ok"):
        return join_res
    _emit_ladder_event("recover_done", "小号已重新上线并完成组队")
    return {"ok": True}


def _stop_small_accounts_after_finish() -> None:
    try:
        res = small_account_manager.stop_all()
        if res.get("ok"):
            _emit_ladder_event("small_stopped", "一键挑战完成，已自动停止小号")
        else:
            _emit_ladder_event("recover_warn", str(res.get("error") or "一键挑战完成后停止小号失败"))
    except Exception as exc:
        _emit_ladder_event("recover_warn", f"一键挑战完成后停止小号失败：{exc}")


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


def _wait_for_main_reconnect(run_id: int, stop_event: threading.Event) -> dict[str, Any]:
    while True:
        if stop_event.is_set():
            return {"ok": False, "stopped": True, "error": "已停止"}
        with _ladder_auto_state.lock:
            if not _ladder_auto_state.running or _ladder_auto_state.run_id != run_id:
                return {"ok": False, "stale": True, "error": "一键挑战已被新的运行替换"}
            pending = _ladder_auto_state.main_disconnect_pending
        if not pending:
            return {"ok": True}
        session = get_session()
        with session._lock:
            connected = bool(session.connected)
        if connected:
            with _ladder_auto_state.lock:
                if _ladder_auto_state.running and _ladder_auto_state.run_id == run_id:
                    _ladder_auto_state.main_disconnect_pending = False
                    _ladder_auto_state.need_small_recovery = True
            return {"ok": True}
        stop_event.wait(_MAIN_DISCONNECT_POLL_S)


def _wait_current_ladder_battle_done(floor: int, run_id: int, stop_event: threading.Event) -> dict[str, Any]:
    deadline = time.time() + _LADDER_WAIT_TIMEOUT_S
    while time.time() < deadline:
        if stop_event.wait(0.3):
            return {"ok": False, "stopped": True, "error": "已停止"}
        with _ladder_auto_state.lock:
            if not _ladder_auto_state.running or _ladder_auto_state.run_id != run_id:
                return {"ok": False, "stale": True, "error": "一键挑战已被新的运行替换"}
            if _ladder_auto_state.main_disconnect_pending:
                return {"ok": False, "main_disconnected": True}
            targets = list(_ladder_auto_state.target_accounts)
        if targets and small_account_manager.any_target_unhealthy(targets):
            with _ladder_auto_state.lock:
                if _ladder_auto_state.running and _ladder_auto_state.run_id == run_id:
                    _ladder_auto_state.need_small_recovery = True
        session = get_session()
        with session._lock:
            state = str(session.battle_state or "")
            last_result = dict(session.battle_last_result or {})
        if state == "error":
            return {"ok": False, "error": str(last_result.get("error") or "战斗异常")}
        if state == "idle":
            return {"ok": True}
    return {"ok": False, "error": f"第 {floor} 层等待战斗结束超时"}


def _run_ladder_auto(run_id: int, start_floor: int, end_floor: int, stop_event: threading.Event) -> None:
    try:
        floor = start_floor
        while floor <= end_floor:
            if stop_event.is_set() or not _is_current_run(run_id):
                break
            with _ladder_auto_state.lock:
                pending = _ladder_auto_state.main_disconnect_pending
                resume_floor = _ladder_auto_state.resume_floor or floor
            if pending:
                wait_main = _wait_for_main_reconnect(run_id, stop_event)
                if not wait_main.get("ok"):
                    return
                reset_battle_state()
                floor = max(floor, resume_floor)
            if _consume_small_recovery_flag(run_id):
                rec = _recover_small_team(run_id, stop_event)
                if not rec.get("ok"):
                    if rec.get("stopped") or rec.get("stale"):
                        return
                    _set_run_error(run_id, str(rec.get("error") or "小号恢复失败"), floor=floor)
                    return
            with _ladder_auto_state.lock:
                if not _ladder_auto_state.running or _ladder_auto_state.run_id != run_id:
                    return
                _ladder_auto_state.current_floor = floor
                _ladder_auto_state.last_error = ""
            _emit_ladder_event("floor_start", f"开始挑战第 {floor} 层", floor=floor)
            res = challenge_ladder({"floor": floor})
            if not res.get("ok"):
                err = str(res.get("error") or "启动失败")
                _set_run_error(run_id, f"第 {floor} 层启动失败：{err}", floor=floor)
                return
            wait_res = _wait_current_ladder_battle_done(floor, run_id, stop_event)
            if not wait_res.get("ok"):
                if wait_res.get("stopped"):
                    _emit_ladder_event("stopped", "一键挑战已停止")
                    return
                if wait_res.get("stale"):
                    return
                if wait_res.get("main_disconnected"):
                    wait_main = _wait_for_main_reconnect(run_id, stop_event)
                    if not wait_main.get("ok"):
                        return
                    reset_battle_state()
                    with _ladder_auto_state.lock:
                        floor = _ladder_auto_state.resume_floor or floor
                    continue
                err = str(wait_res.get("error") or "战斗失败")
                _set_run_error(run_id, err, floor=floor)
                return
            if _consume_small_recovery_flag(run_id):
                rec = _recover_small_team(run_id, stop_event)
                if not rec.get("ok"):
                    if rec.get("stopped") or rec.get("stale"):
                        return
                    _set_run_error(run_id, str(rec.get("error") or "小号恢复失败"), floor=floor)
                    return
            with _ladder_auto_state.lock:
                if not _ladder_auto_state.running or _ladder_auto_state.run_id != run_id:
                    return
                _ladder_auto_state.completed_floor = floor
            _emit_ladder_event("floor_done", f"第 {floor} 层完成", floor=floor)
            floor += 1
            if floor <= end_floor:
                stop_event.wait(1.0)
        if not stop_event.is_set() and _is_current_run(run_id):
            _emit_ladder_event("finished", f"一键挑战完成：{start_floor}~{end_floor} 层")
            _stop_small_accounts_after_finish()
    finally:
        with _ladder_auto_state.lock:
            if _ladder_auto_state.run_id == run_id:
                _ladder_auto_state.running = False
                _ladder_auto_state.main_disconnect_pending = False


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

    targets = _snapshot_target_accounts()
    with _ladder_auto_state.lock:
        _ladder_auto_state.run_id += 1
        run_id = _ladder_auto_state.run_id
        stop_event = threading.Event()
        _ladder_auto_state.running = True
        _ladder_auto_state.start_floor = start_floor
        _ladder_auto_state.end_floor = end_floor
        _ladder_auto_state.current_floor = 0
        _ladder_auto_state.completed_floor = 0
        _ladder_auto_state.last_error = ""
        _ladder_auto_state.need_small_recovery = False
        _ladder_auto_state.main_disconnect_pending = False
        _ladder_auto_state.resume_floor = start_floor
        _ladder_auto_state.target_accounts = targets
        _ladder_auto_state.stop_event = stop_event
    _ladder_auto_thread = threading.Thread(
        target=_run_ladder_auto,
        args=(run_id, start_floor, end_floor, stop_event),
        daemon=True,
        name="ladder-auto",
    )
    _ladder_auto_thread.start()
    _emit_ladder_event("started", f"一键挑战已启动：{start_floor}~{end_floor} 层", start_floor=start_floor, end_floor=end_floor)
    return _ladder_auto_state.snapshot()


def stop_ladder_auto() -> dict[str, Any]:
    with _ladder_auto_state.lock:
        stop_event = _ladder_auto_state.stop_event
        _ladder_auto_state.run_id += 1
        _ladder_auto_state.running = False
        _ladder_auto_state.main_disconnect_pending = False
        _ladder_auto_state.need_small_recovery = False
    stop_event.set()
    _emit_ladder_event("stopped", "已请求停止一键挑战")
    return _ladder_auto_state.snapshot()


def get_ladder_auto_status() -> dict[str, Any]:
    return _ladder_auto_state.snapshot()


def on_main_disconnect_during_ladder() -> None:
    with _ladder_auto_state.lock:
        if not _ladder_auto_state.running:
            return
        floor = _ladder_auto_state.current_floor or _ladder_auto_state.resume_floor or _ladder_auto_state.start_floor
        _ladder_auto_state.resume_floor = floor
        _ladder_auto_state.main_disconnect_pending = True
        _ladder_auto_state.need_small_recovery = True
    _emit_ladder_event("main_disconnected", f"主号断线，一键挑战暂停于第 {floor} 层，等待重连")


def on_main_reconnect_resume() -> None:
    global _ladder_auto_thread
    with _ladder_auto_state.lock:
        if not _ladder_auto_state.running or not _ladder_auto_state.main_disconnect_pending:
            return
        _ladder_auto_state.main_disconnect_pending = False
        _ladder_auto_state.need_small_recovery = True
        run_id = _ladder_auto_state.run_id
        stop_event = _ladder_auto_state.stop_event
        resume = _ladder_auto_state.resume_floor or _ladder_auto_state.start_floor
        end_floor = _ladder_auto_state.end_floor
        thread_alive = _ladder_auto_thread is not None and _ladder_auto_thread.is_alive()
    reset_battle_state()
    _emit_ladder_event("main_reconnected", f"主号已重连，从第 {resume} 层继续一键挑战", floor=resume)

    if not thread_alive:
        _ladder_auto_thread = threading.Thread(
            target=_run_ladder_auto,
            args=(run_id, resume, end_floor, stop_event),
            daemon=True,
            name="ladder-auto-resume",
        )
        _ladder_auto_thread.start()


def abort_ladder_auto(error: str) -> dict[str, Any]:
    message = str(error or "一键挑战已停止")
    with _ladder_auto_state.lock:
        if not _ladder_auto_state.running:
            stop_event = None
        else:
            stop_event = _ladder_auto_state.stop_event
            _ladder_auto_state.run_id += 1
            _ladder_auto_state.running = False
            _ladder_auto_state.main_disconnect_pending = False
            _ladder_auto_state.need_small_recovery = False
            _ladder_auto_state.last_error = message
    if stop_event is None:
        return _ladder_auto_state.snapshot()
    stop_event.set()
    _emit_ladder_event("error", message)
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
    res = small_account_manager.start_first_two(list(account_res.get("items", [])))
    if res.get("ok") and is_ladder_auto_running():
        with _ladder_auto_state.lock:
            _refresh_target_accounts_locked()
    return res


def start_small_account(account: str) -> dict[str, Any]:
    account_res = list_small_accounts()
    if not account_res.get("ok"):
        return account_res
    items = list(account_res.get("items", []))
    target = next((item for item in items if str(item.get("account", "")).strip() == str(account or "").strip()), None)
    if not target:
        return {"ok": False, "error": "小号不存在"}
    res = small_account_manager.start_one(target)
    if res.get("ok") and is_ladder_auto_running():
        with _ladder_auto_state.lock:
            _refresh_target_accounts_locked()
    return res


def stop_small_accounts() -> dict[str, Any]:
    return small_account_manager.stop_all()


def stop_small_account(account: str) -> dict[str, Any]:
    return small_account_manager.stop_one(account)


def get_small_status() -> dict[str, Any]:
    return {"ok": True, "items": small_account_manager.status()}
