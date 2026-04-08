"""
战斗功能（预留桩）。

摸索流程：
1. 在 main-000.py 中手动触发战斗操作（如点击挑战/攻击按钮）。
2. 同时在测试应用"报文探测"面板中观察此时 UP 方向的报文。
3. 对捕获到的未知报文先打标注（如"点击挑战时发出"），
   或直接通过 POST /api/probe/send 重放验证。
4. 确认报文有效后，填入下方模板并实现 start_battle() 函数。

已知线索（来自 main-000.py _on_team 注释，可能与战斗相关）：
  - "18000000e803030044289605f6054728000006000000e00000000000"
  - "2d000000e8030100fa0700000000120800001b00000001001500e4b88de59ca8e5908ce4b880e59cb0e59bbe2e2e2e0000"
  上述报文尚未验证，使用前请先通过抓包确认语义。
"""

from core.connector import enqueue_packet
from core.session import get_session


def start_battle(target_hex: str = "") -> dict:
    """
    发起战斗（占位实现，待抓包后填入真实报文模板）。

    Args:
        target_hex: 目标信息 hex（暂未知格式，通过 packet_probe 摸索后填入）

    Returns:
        {'ok': False, 'error': '功能待实现'}
    """
    session = get_session()
    if not session.connected:
        return {"ok": False, "error": "未连接游戏服"}

    # TODO: 通过 packet_probe 捕获真实报文后，替换下方 packet_hex
    # packet_hex = "..."
    # enqueue_packet(session.send_queue, packet_hex, priority=10)
    # return {"ok": True, "queued": 1}

    return {"ok": False, "error": "战斗功能待实现，请先通过 packet_probe 摸索报文格式"}
