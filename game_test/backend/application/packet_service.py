"""HTTP-facing packet/log/probe façade."""

from typing import Any

from game_test.backend.domain.packet import probe
from game_test.backend.runtime import get_session


def list_packets(
    *,
    limit: int,
    direction: str | None,
    parsed_only: bool | None,
    annotated_only: bool | None,
) -> dict[str, Any]:
    records = get_session().get_packet_log(
        limit=limit,
        direction=direction,
        parsed_only=parsed_only,
        annotated_only=annotated_only,
    )
    return {"ok": True, "total": len(records), "packets": records}


def annotate_packet(packet_id: int, text: str) -> dict[str, Any]:
    return probe.annotate_packet(packet_id, text)


def get_fingerprints() -> dict[str, Any]:
    return {"ok": True, "fingerprints": probe.get_all_fingerprints()}


def update_fingerprint(fp: str, description: str) -> dict[str, Any]:
    clean_fp = str(fp or "").strip().lower()
    clean_description = str(description or "").strip()
    if not clean_description:
        return {"ok": False, "error": "description 不能为空"}
    with probe._fingerprints_lock:
        probe._fingerprints[clean_fp] = clean_description
    probe._save_fingerprints()
    return {"ok": True, "fingerprint": clean_fp, "description": clean_description}


def delete_fingerprint(fp: str) -> dict[str, Any]:
    clean_fp = str(fp or "").strip().lower()
    with probe._fingerprints_lock:
        removed = probe._fingerprints.pop(clean_fp, None)
    probe._save_fingerprints()
    return {"ok": True, "removed": removed is not None}


def send_probe_packet(*, hex_str: str, use_queue: bool, priority: int) -> dict[str, Any]:
    return probe.send_probe_packet(hex_str, use_queue=use_queue, priority=priority)


def parse_probe_packet(hex_str: str) -> dict[str, Any]:
    return {"ok": True, "parsed": probe.try_parse_packet(hex_str)}
