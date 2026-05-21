"""天梯挑战与组队邀请协议。"""

import re

from game_test.utils.random_num import random_num_hex4

LADDER_MIN_FLOOR = 1
LADDER_MAX_FLOOR = 20
_ROLE_ID_RE = re.compile(r"^[0-9a-f]{6}$")


def normalize_role_id(role_id: str) -> str:
    clean = str(role_id or "").strip().lower()
    if not _ROLE_ID_RE.fullmatch(clean):
        raise ValueError("角色 ID 必须是 6 位 hex")
    return clean


def build_ladder_challenge_packet(floor: int) -> dict[str, str | int]:
    floor_num = int(floor)
    if floor_num < LADDER_MIN_FLOOR or floor_num > LADDER_MAX_FLOOR:
        raise ValueError("楼层范围必须是 1~20")
    target = 0x2AF8 + floor_num
    target_id_le = target.to_bytes(2, "little").hex()
    rand16 = random_num_hex4()
    packet_hex = (
        "1b000000e8030500f603"
        + rand16
        + "f505fc030000090000000100"
        + target_id_le
        + "0000000000"
    )
    return {
        "floor": floor_num,
        "target": target,
        "target_id_le": target_id_le,
        "random_num": rand16,
        "packet_hex": packet_hex,
    }


def build_team_invite_packet(target_user_id: str) -> dict[str, str]:
    user_id = normalize_role_id(target_user_id)
    packet_hex = "18000000e80307001204f9f7f5051504000006000000" + user_id + "000000"
    return {"target_user_id": user_id, "packet_hex": packet_hex}


def build_team_accept_packet() -> str:
    return "15000000e80307001304faf5f5051304000003000000000000"


def build_small_battle_ack_packet() -> str:
    return "1f000000e8030500f703" + random_num_hex4() + "f505010400000d00000003000000000000000000000000"

