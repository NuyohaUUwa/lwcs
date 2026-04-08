"""
组队功能（预留桩）。

摸索流程：
1. 在 main-000.py 中手动触发组队操作（发起邀请 / 接受邀请）。
2. 在测试应用"报文探测"面板中捕获 UP 方向报文，打标注记录操作类型。
3. 通过 POST /api/probe/send 重放报文，观察 DN 方向的服务器响应。
4. 确认后填入下方模板并实现对应函数。

已知线索（来自 main-000.py _on_team，已注释，仅供参考）：
  - "18000000e803030044289605f6054728000006000000e00000000000"  （疑似发起组队请求）
  - "2d000000e8030100fa0700000000..."  （疑似组队相关消息）
"""

from core.connector import enqueue_packet
from core.session import get_session


def invite_team(target_role_id: str = "") -> dict:
    """发起组队邀请（占位）。"""
    session = get_session()
    if not session.connected:
        return {"ok": False, "error": "未连接游戏服"}
    # TODO: 填入真实报文模板
    return {"ok": False, "error": "组队功能待实现，请先通过 packet_probe 摸索报文格式"}


def accept_team(invite_id: str = "") -> dict:
    """接受组队邀请（占位）。"""
    session = get_session()
    if not session.connected:
        return {"ok": False, "error": "未连接游戏服"}
    return {"ok": False, "error": "组队功能待实现，请先通过 packet_probe 摸索报文格式"}
