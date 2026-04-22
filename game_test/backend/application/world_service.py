"""HTTP-facing world and movement façade."""

from typing import Any

from game_test.backend.domain.travel.map_npc_parse import extract_map_npc_list
from game_test.backend.domain.travel.teleport import get_teleport_destinations
from game_test.backend.runtime import get_session
from game_test.backend.runtime.actions import send_action


def send_chat(payload: dict[str, Any]) -> dict[str, Any]:
    return send_action("chat.send", payload)


def get_teleport_destinations_snapshot() -> dict[str, Any]:
    return {"ok": True, "items": get_teleport_destinations()}


def teleport(payload: dict[str, Any]) -> dict[str, Any]:
    return send_action("teleport.go", payload)


def parse_map_npc_packet(raw_hex: str) -> dict[str, Any]:
    npc_list = extract_map_npc_list(raw_hex)
    hit = npc_list[0] if npc_list else None
    return {"ok": True, "map_npc": hit, "map_npc_list": npc_list}


def set_current_map_npc(*, id_hex: str, utf8_text: str) -> dict[str, Any]:
    clean_id_hex = str(id_hex or "").strip().lower()
    clean_utf8_text = str(utf8_text or "").strip()
    if len(clean_id_hex) != 8:
        return {"ok": False, "error": "id_hex 须为 8 位 hex"}
    try:
        _ = int(clean_id_hex, 16)
    except ValueError:
        return {"ok": False, "error": "id_hex 非法"}
    get_session().set_current_map_npc(clean_id_hex, clean_utf8_text)
    return {"ok": True}
