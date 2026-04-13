"""
报文探测模块：
- 记录全量收发报文（PacketRecord）
- 自动解析：已知指纹 -> 详细字段；通用帧头 -> command + UTF-8 文本；无法解析 -> None
- 指纹描述表：内置 + 用户新增，统一持久化到 data/fingerprints.json
- 自定义发包通过 action_manager 统一发送
"""

import json
import os
import time
import threading
import binascii
import struct
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any

from core.codec import extract_utf8_segments, extract_packet_fingerprint
from core.session import get_session
from paths import FINGERPRINTS_FILE as _FINGERPRINTS_FILE
from services.packet_log_service import append_packet_record

# ------------------------------------------------------------------ #
#  指纹描述表（内置 + 用户新增，持久化到 paths.FINGERPRINTS_FILE）        #
# ------------------------------------------------------------------ #

# 内置默认指纹（文件不存在或被误删时使用）
_DEFAULT_FINGERPRINTS: Dict[str, str] = {
    "e8030100ec07": "背包变化/已使用礼包",
    "e8030100ed07": "获得物品/属性刷新",
    "e8030100e207": "战斗结算经验金币",
    "e8030100f207": "世界频道/系统公告",
    "e80301005151": "物品信息片段",
    "e8030100d607": "背包物品列表",
    "e8030100514f": "服务器心跳(忽略)",
}

_fingerprints: Dict[str, str] = {}
_fingerprints_lock = threading.Lock()


def _load_fingerprints():
    """启动时从文件加载指纹描述表；若文件不存在则以内置默认值初始化并写入。"""
    global _fingerprints
    os.makedirs(os.path.dirname(_FINGERPRINTS_FILE), exist_ok=True)
    if os.path.exists(_FINGERPRINTS_FILE):
        try:
            with open(_FINGERPRINTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            loaded = {str(k).lower(): str(v) for k, v in data.items()}
            # 补入内置条目（防止用户删除关键指纹后解析失效）
            for fp, desc in _DEFAULT_FINGERPRINTS.items():
                loaded.setdefault(fp, desc)
            _fingerprints = loaded
        except Exception as e:
            print(f"[probe] 加载指纹文件失败，使用内置默认值: {e}")
            _fingerprints = dict(_DEFAULT_FINGERPRINTS)
    else:
        _fingerprints = dict(_DEFAULT_FINGERPRINTS)
        _save_fingerprints()


def _save_fingerprints():
    """将内存中的指纹描述表持久化到文件。"""
    try:
        os.makedirs(os.path.dirname(_FINGERPRINTS_FILE), exist_ok=True)
        with _fingerprints_lock:
            data = dict(_fingerprints)
        with open(_FINGERPRINTS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[probe] 保存指纹文件失败: {e}")


def get_all_fingerprints() -> Dict[str, str]:
    """返回当前内存中的全部指纹描述表副本。"""
    with _fingerprints_lock:
        return dict(_fingerprints)


# 向外暴露（兼容任何外部引用 KNOWN_FINGERPRINTS 的代码）
KNOWN_FINGERPRINTS = _fingerprints  # 同一对象引用

# 初始化时加载
_load_fingerprints()

# 写入 packet_log / 会话 jsonl 时排除的指纹（与 heartbeat.HEARTBEAT_PACKET_TEMPLATE、fingerprints 表一致）
_PACKET_LOG_SKIP_FINGERPRINTS: frozenset[str] = frozenset(
    {
        "e8030100514f",  # 服务器心跳（下行）
        "e8030a000a04",  # 客户端心跳模板 e8030a000a04（上行；偶见与业务同批下行）
        "e80302000504",  # 另一路客户端心跳（fingerprints.json）
        "e8030100f207",  # 世界频道/系统公告
    }
)


# ------------------------------------------------------------------ #
#  数据结构                                                             #
# ------------------------------------------------------------------ #
_id_counter = 0
_id_lock = threading.Lock()


def _next_id() -> int:
    global _id_counter
    with _id_lock:
        _id_counter += 1
        return _id_counter


@dataclass
class PacketRecord:
    """单条报文记录。"""
    id: int
    ts: float                       # Unix 时间戳
    direction: str                  # "UP" 上行（发送）/ "DN" 下行（接收）
    raw_hex: str                    # 原始 hex 字符串（小写）
    fingerprint: str                # raw_hex[8:20]
    parsed: Optional[Dict[str, Any]] = field(default=None)   # 解析结果或 None
    annotation: str = ""            # 指纹描述（来自指纹表，自动填充）

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["ts_str"] = time.strftime("%H:%M:%S", time.localtime(self.ts))
        return d


# ------------------------------------------------------------------ #
#  解析逻辑                                                             #
# ------------------------------------------------------------------ #

def try_parse_packet(raw_hex: str) -> Optional[Dict[str, Any]]:
    """
    尝试解析报文，按优先级：
    1. 已知指纹 → 返回 {level:'known', type, fingerprint, utf8_text}
    2. 通用帧头 → 返回 {level:'generic', command_hex, content_length, utf8_text}
    3. 无法解析 → 返回 None
    """
    if not raw_hex or len(raw_hex) < 16:
        return None

    fingerprint = extract_packet_fingerprint(raw_hex)

    # ---------- 1. 已知指纹（精确匹配 + 后4位子串匹配）----------
    fp_desc = None
    with _fingerprints_lock:
        fp_desc = _fingerprints.get(fingerprint)
        if fp_desc is None:
            for fp, desc in _fingerprints.items():
                if fp[-4:] in raw_hex[8:20]:
                    fp_desc = desc
                    break

    if fp_desc is not None:
        utf8_text = extract_utf8_segments(raw_hex)
        return {
            "level": "known",
            "type": fp_desc,
            "fingerprint": fingerprint,
            "utf8_text": utf8_text,
        }

    # ---------- 2. 通用帧头解析 ----------
    try:
        byte_data = bytes.fromhex(raw_hex)
        if len(byte_data) >= 8:
            content_length = struct.unpack('<I', byte_data[0:4])[0]
            command = struct.unpack('<I', byte_data[4:8])[0]
            utf8_text = extract_utf8_segments(raw_hex)
            return {
                "level": "generic",
                "command_hex": f"0x{command:08X}",
                "content_length": content_length,
                "utf8_text": utf8_text,
            }
    except Exception:
        pass

    # ---------- 3. 无法解析 ----------
    return None


# ------------------------------------------------------------------ #
#  记录 & 指纹描述存储                                                  #
# ------------------------------------------------------------------ #

def record_packet(raw_bytes_or_hex, direction: str) -> Optional[PacketRecord]:
    """
    记录一条报文（上行或下行），自动解析并追加到 session 的 packet_log。
    annotation 字段从指纹描述表自动填入。
    心跳类报文不写内存日志与 jsonl，返回 None。
    """
    if isinstance(raw_bytes_or_hex, bytes):
        raw_hex = raw_bytes_or_hex.hex()
    else:
        raw_hex = raw_bytes_or_hex.lower().replace(" ", "")

    fingerprint = extract_packet_fingerprint(raw_hex)
    if fingerprint in _PACKET_LOG_SKIP_FINGERPRINTS:
        return None
    parsed = try_parse_packet(raw_hex)
    record_id = _next_id()

    # 从指纹描述表自动填入 annotation
    with _fingerprints_lock:
        annotation = _fingerprints.get(fingerprint, "")

    record = PacketRecord(
        id=record_id,
        ts=time.time(),
        direction=direction.upper(),
        raw_hex=raw_hex,
        fingerprint=fingerprint,
        parsed=parsed,
        annotation=annotation,
    )

    record_dict = record.to_dict()
    session = get_session()
    session.append_packet(record_dict)
    append_packet_record(record_dict)
    return record


def annotate_packet(packet_id: int, text: str) -> dict:
    """
    为指定 id 的报文添加/更新指纹描述。

    效果：
    - 更新内存指纹表 + 持久化到 fingerprints.json
    - session 中所有相同指纹报文的 annotation 字段同步更新
    - 广播 SSE 'annotation' 事件（携带 fingerprint，供前端批量更新同类行）

    Returns:
        {'ok': True, 'fingerprint': '...', 'annotation': '...'} 或 {'ok': False, 'error': ...}
    """
    session = get_session()

    # 找到报文，取其指纹
    fingerprint = None
    with session._lock:
        for record in session._packet_log:
            if record.get("id") == packet_id:
                fingerprint = record.get("fingerprint", "")
                break

    if fingerprint is None:
        return {"ok": False, "error": f"未找到 id={packet_id} 的报文记录"}

    # 更新指纹描述表
    with _fingerprints_lock:
        if text:
            _fingerprints[fingerprint] = text
        else:
            _fingerprints.pop(fingerprint, None)
    _save_fingerprints()

    # 更新 session 中所有同指纹报文的 annotation（及 parsed.type）
    with session._lock:
        for record in session._packet_log:
            if record.get("fingerprint") == fingerprint:
                record["annotation"] = text
                parsed = record.get("parsed")
                if isinstance(parsed, dict) and parsed.get("level") == "known":
                    parsed["type"] = text

    # 广播 SSE：携带 fingerprint，前端批量更新同类行
    session._notify_sse("annotation", {
        "fingerprint": fingerprint,
        "annotation": text,
    })
    return {"ok": True, "fingerprint": fingerprint, "annotation": text}


# ------------------------------------------------------------------ #
#  自定义发包（探索新功能）                                              #
# ------------------------------------------------------------------ #

def send_probe_packet(hex_str: str, use_queue: bool = True, priority: int = 10) -> dict:
    """
    发送自定义 hex 报文，用于探索战斗/组队/传送等未知功能。

    Args:
        hex_str:    要发送的 hex 字符串（忽略空格）
        use_queue:  True 走发送队列（节流 0.6s），False 直接发（立即）
        priority:   队列优先级（仅 use_queue=True 时生效）

    Returns:
        {'ok': True, 'hex': '...'}  或  {'ok': False, 'error': '...'}
    """
    clean_hex = hex_str.replace(" ", "").replace("\n", "").lower()
    if len(clean_hex) % 2 != 0:
        return {"ok": False, "error": "hex 长度必须为偶数"}
    try:
        binascii.unhexlify(clean_hex)
    except binascii.Error as e:
        return {"ok": False, "error": f"hex 格式错误: {e}"}
    from services.action_manager import send_raw_action

    result = send_raw_action(clean_hex, priority=priority, use_queue=use_queue)
    if result.get("ok"):
        result["hex"] = clean_hex
    return result
