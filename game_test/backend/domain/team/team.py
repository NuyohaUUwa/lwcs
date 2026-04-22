"""组队功能（预留桩）。"""

from game_test.backend.runtime import get_session


def invite_team(target_role_id: str = "") -> dict[str, object]:
    """发起组队邀请（占位）。"""
    session = get_session()
    if not session.connected:
        return {"ok": False, "error": "未连接游戏服"}
    # TODO: 填入真实报文模板
    return {"ok": False, "error": "组队功能待实现，请先通过 packet_probe 摸索报文格式"}


def accept_team(invite_id: str = "") -> dict[str, object]:
    """接受组队邀请（占位）。"""
    session = get_session()
    if not session.connected:
        return {"ok": False, "error": "未连接游戏服"}
    return {"ok": False, "error": "组队功能待实现，请先通过 packet_probe 摸索报文格式"}
