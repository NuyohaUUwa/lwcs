"""
地图传送功能。
"""

import json

from paths import TELEPORT_DESTINATIONS_FILE as DESTINATIONS_FILE
from utils.random_num import random_num_hex4
_TELEPORT_PACKET_TEMPLATE = "18000000e80303004428{random_num}f5054728000006000000{destination}0000"


def _load_destinations() -> dict[str, str]:
    try:
        with open(DESTINATIONS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}

    out: dict[str, str] = {}
    for name, code in raw.items():
        key = str(name or "").strip()
        value = str(code or "").strip().lower()
        if not key or len(value) != 8:
            continue
        try:
            int(value, 16)
        except ValueError:
            continue
        out[key] = value
    return out


def get_teleport_destinations() -> list[dict]:
    destinations = _load_destinations()
    return [{"name": name, "code": code} for name, code in destinations.items()]


def _normalize_destination(destination: str) -> tuple[str, str]:
    clean = str(destination or "").strip()
    if not clean:
        raise ValueError("destination 不能为空")

    destinations = _load_destinations()
    if clean in destinations:
        return clean, destinations[clean]

    code = clean.lower()
    if len(code) != 8:
        raise ValueError("地点编号必须是 8 位 hex，或传入 teleport_destination.json 中已配置的地点名称")
    try:
        int(code, 16)
    except ValueError as e:
        raise ValueError("地点编号不是合法 hex") from e

    for name, mapped_code in destinations.items():
        if mapped_code == code:
            return name, code
    return clean, code


def build_teleport_packet(destination: str) -> dict:
    destination_name, destination_code = _normalize_destination(destination)
    random_num = random_num_hex4()
    return {
        "packet_hex": _TELEPORT_PACKET_TEMPLATE.format(
            random_num=random_num,
            destination=destination_code,
        ),
        "destination_name": destination_name,
        "destination_code": destination_code,
        "random_num": random_num,
    }
