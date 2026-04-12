"""
角色属性解析：从下行 TLV 区提取「属性名：属性值」（全角冒号）。

常见承载指纹：d607（背包/选角）、e8030100ed07（获得物品/属性刷新等）。
"""

from typing import Dict
from core.session import get_session

# 需要提取并展示的属性名（按显示顺序）
STAT_NAMES = [
    '力量', '智力', '敏捷', '体质',
    '物攻', '物防', '法攻', '法防',
    '命中', '躲闪', '暴击', '速度',
    '等级', '职业', '声望', '积分',
    '月VIP', '周VIP', '任务积分', '金库次数', '珍珑宝库次数',
    '经验UP', '攻击UP', '金钱UP', '回血', '回蓝',
]
_STAT_SET = set(STAT_NAMES)

# 属性分组，用于前端分区展示
STAT_GROUPS = {
    '基础属性': ['力量', '智力', '敏捷', '体质'],
    '战斗属性': ['物攻', '物防', '法攻', '法防', '命中', '躲闪', '暴击', '速度'],
    '角色信息': ['等级', '职业', '声望', '积分'],
    '其他信息': ['月VIP', '周VIP', '任务积分', '金库次数', '珍珑宝库次数',
               '经验UP', '攻击UP', '金钱UP', '回血', '回蓝'],
}


def parse_role_stats(packet_hex: str) -> Dict[str, str]:
    """
    从报文 hex 中提取角色属性键值对（与指纹无关，按正文 TLV 扫描）。
    使用 [2字节LE长度][UTF-8内容] 格式逐段扫描，提取包含全角冒号的条目。

    Returns:
        {'力量': '983', '智力': '583', ...}  或空 dict（解析失败）
    """
    try:
        data = bytes.fromhex(packet_hex)
    except Exception:
        return {}

    stats: Dict[str, str] = {}
    i = 0
    found_start = False  # 是否已经遇到 "力量"

    while i < len(data) - 2:
        length = int.from_bytes(data[i:i + 2], 'little')
        if 1 <= length <= 120 and i + 2 + length <= len(data):
            chunk = data[i + 2: i + 2 + length]
            try:
                text = chunk.decode('utf-8')
                if '\uff1a' in text:
                    name, _, value = text.partition('\uff1a')
                    name = name.strip()
                    value = value.strip()
                    if name in _STAT_SET:
                        found_start = True
                        stats[name] = value
                    elif found_start and name:
                        # 遇到不在集合中的属性，说明已过属性区段
                        pass
                    i += 2 + length
                    continue
            except (UnicodeDecodeError, ValueError):
                pass
        i += 1

    return stats


def update_session_stats(packet_hex: str) -> bool:
    """
    解析报文并整表替换 GameSession.role_stats（如 d607 全量属性），广播 SSE。
    返回 True 表示成功解析到属性数据。
    """
    stats = parse_role_stats(packet_hex)
    if not stats:
        return False

    session = get_session()
    with session._lock:
        session.role_stats = stats
    session._notify_sse("role_stats", {
        "stats": stats,
        "groups": STAT_GROUPS,
        "order": STAT_NAMES,
    })
    return True


def merge_role_stats_from_packet(packet_hex: str) -> bool:
    """
    若报文中含已知角色属性 TLV，则合并写入 session（仅覆盖解析到的键）。
    无属性内容时不改 session、不广播。用于 e8030100ed07 等。
    """
    stats = parse_role_stats(packet_hex)
    if not stats:
        return False

    session = get_session()
    with session._lock:
        merged = dict(session.role_stats)
        merged.update(stats)
        session.role_stats = merged
        out = dict(merged)
    session._notify_sse("role_stats", {
        "stats": out,
        "groups": STAT_GROUPS,
        "order": STAT_NAMES,
    })
    return True
