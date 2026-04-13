"""
角色属性：从下行报文 hex 解析已知属性，写入会话并经 SSE 推送到前端。

自报文字节偏移 0x004B 起，按「小端 uint16 长度 + UTF-8 负载」的 LV/TLV 链逐项读取。
某一项若长度越界或 UTF-8 无法严格解码，则仅将当前读指针前移 1 字节，再尝试下一项。
负载文本内可出现多个「属性名：值」（全角或半角冒号），由 extract_stat_pairs_from_text 拆键。
"""

from __future__ import annotations

import re
from typing import Dict

from core.session import get_session

ROLE_STATS_LV_OFFSET = 0x004B

STAT_NAMES = [
    '力量', '智力', '敏捷', '体质',
    '物攻', '物防', '法攻', '法防',
    '命中', '躲闪', '暴击', '速度',
    '等级', '职业', '声望', '积分',
    '月VIP', '周VIP', '任务积分', '金库次数', '珍珑宝库次数',
    '经验UP', '攻击UP', '金钱UP', '回血', '回蓝',
]

STAT_GROUPS = {
    '角色信息': ['等级', '职业', '声望', '积分'],
    '基础属性': ['力量', '智力', '敏捷', '体质'],
    '战斗属性': ['物攻', '物防', '法攻', '法防', '命中', '躲闪', '暴击', '速度'],
    '其他信息': [
        '月VIP', '周VIP', '任务积分', '金库次数', '珍珑宝库次数',
        '经验UP', '攻击UP', '金钱UP', '回血', '回蓝',
    ],
}

_STAT_NAME_ALT = '|'.join(re.escape(n) for n in sorted(STAT_NAMES, key=len, reverse=True))
_STAT_PAIR_RE = re.compile(rf'({_STAT_NAME_ALT})(?:\uff1a|:(?=\s*\S))')


def _normalize_text(s: str) -> str:
    return s.replace('\x00', '').replace('\ufeff', '').strip()


def extract_stat_pairs_from_text(text: str) -> Dict[str, str]:
    """
    从一段 UTF-8 解码后的文本中取出所有已知属性键值对。
    同一段内多个「力量：8857 智力：2450」按长名优先的正则依次切分。
    """
    text = _normalize_text(text)
    if not text:
        return {}
    if '\uff1a' not in text and ':' not in text:
        return {}

    matches = list(_STAT_PAIR_RE.finditer(text))
    if not matches:
        return {}

    out: Dict[str, str] = {}
    for j, m in enumerate(matches):
        name = m.group(1)
        start = m.end()
        end = matches[j + 1].start() if j + 1 < len(matches) else len(text)
        out[name] = text[start:end].strip()
    return out


def parse_role_stats(packet_hex: str) -> Dict[str, str]:
    """
    从 offset 0x004B 起读取 LV 链：uint16 LE 长度 + 定长 UTF-8 负载；合并各负载中解析出的属性。
    无法解码当前位置时 i += 1 再试（对齐前导控制字节）。
    """
    try:
        data = bytes.fromhex(packet_hex)
    except Exception:
        return {}

    if len(data) < ROLE_STATS_LV_OFFSET + 2:
        return {}

    stats: Dict[str, str] = {}
    i = ROLE_STATS_LV_OFFSET

    while i + 2 <= len(data):
        length = int.from_bytes(data[i : i + 2], 'little')
        end = i + 2 + length

        if length < 1 or end > len(data):
            i += 1
            continue

        chunk = data[i + 2 : end]
        try:
            text = chunk.decode('utf-8')
        except UnicodeDecodeError:
            i += 1
            continue

        stats.update(extract_stat_pairs_from_text(text))
        i = end

    return stats


def update_session_stats(packet_hex: str) -> bool:
    stats = parse_role_stats(packet_hex)
    if not stats:
        return False

    session = get_session()
    with session._lock:
        session.role_stats = dict(stats)
    session._notify_sse('role_stats', {
        'stats': dict(stats),
        'groups': STAT_GROUPS,
        'order': STAT_NAMES,
    })
    return True


def merge_role_stats_from_packet(packet_hex: str) -> bool:
    stats = parse_role_stats(packet_hex)
    if not stats:
        return False

    session = get_session()
    with session._lock:
        merged = dict(session.role_stats)
        merged.update(stats)
        session.role_stats = merged
        out = dict(merged)
    session._notify_sse('role_stats', {
        'stats': out,
        'groups': STAT_GROUPS,
        'order': STAT_NAMES,
    })
    return True
