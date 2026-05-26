"""HTTP-facing battle façade."""

from typing import Any

from game_test.backend.domain.combat.battle import get_auto_use_rules, set_auto_use_rules
from game_test.backend.infrastructure.data_manager import get_monsters, save_monsters
from game_test.backend.runtime.actions import send_action


def start_battle(payload: dict[str, Any]) -> dict[str, Any]:
    return send_action("battle.start", payload)


def do_battle() -> dict[str, Any]:
    return send_action("battle.do", {})


def one_shot_battle(payload: dict[str, Any]) -> dict[str, Any]:
    return send_action("battle.one_shot", payload)


def start_battle_loop(payload: dict[str, Any]) -> dict[str, Any]:
    return send_action("battle.loop.start", payload)


def stop_battle_loop(payload: dict[str, Any]) -> dict[str, Any]:
    return send_action("battle.loop.stop", payload)


def get_monster_list() -> dict[str, Any]:
    return {"ok": True, "monsters": get_monsters()}


def upsert_monster(*, name: str, code: str) -> dict[str, Any]:
    clean_name = str(name or "").strip()
    clean_code = str(code or "").strip().lower()
    if not clean_name:
        return {"ok": False, "error": "name 不能为空"}
    if not clean_code:
        return {"ok": False, "error": "code 不能为空"}

    monsters = get_monsters()
    exists = next((monster for monster in monsters if monster.get("code") == clean_code), None)
    if exists:
        exists["name"] = clean_name
    else:
        monsters.append({"name": clean_name, "code": clean_code})
    save_monsters(monsters)
    return {"ok": True, "monsters": monsters}


def delete_monster(code: str) -> dict[str, Any]:
    clean_code = str(code or "").strip().lower()
    monsters = get_monsters()
    new_list = [monster for monster in monsters if (monster.get("code") or "").lower() != clean_code]
    if len(new_list) == len(monsters):
        return {"ok": False, "error": "怪物代码不存在"}
    save_monsters(new_list)
    return {"ok": True, "monsters": new_list}


def get_auto_use_config() -> dict[str, Any]:
    return {"ok": True, "rules": get_auto_use_rules()}


def update_auto_use_config(rules: Any) -> dict[str, Any]:
    if not isinstance(rules, list):
        return {"ok": False, "error": "rules 必须是数组"}
    return {"ok": True, "rules": set_auto_use_rules(rules)}
