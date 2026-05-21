"""HTTP-facing ladder façade."""

import re
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


def _current_main_account() -> str:
    session = get_session()
    return str(session.account or "").strip()


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
