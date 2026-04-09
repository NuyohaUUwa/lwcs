"""
地图传送功能（预留桩）。

摸索流程：
1. 在 main-000.py 中手动点击地图传送入口。
2. 在测试应用"报文探测"面板中记录 UP 方向发出的报文，打标注如"传送到XX地图"。
3. 对比不同目标地图的报文差异，找出地图 ID 的编码规律。
4. 通过 POST /api/probe/send 验证后，填入下方模板实现 teleport() 函数。
"""

from core.session import get_session


def teleport(map_id: str = "") -> dict:
    """
    地图传送（占位）。

    Args:
        map_id: 目标地图 ID（格式待通过 packet_probe 确认）

    Returns:
        {'ok': False, 'error': '功能待实现'}
    """
    session = get_session()
    if not session.connected:
        return {"ok": False, "error": "未连接游戏服"}
    # TODO: 填入真实报文模板
    return {"ok": False, "error": "传送功能待实现，请先通过 packet_probe 摸索报文格式"}
