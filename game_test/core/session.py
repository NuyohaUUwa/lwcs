"""
全局游戏会话单例。
持有 TCP socket、session_id、背包数据、报文日志、SSE 事件队列等状态。
所有 feature 模块通过 get_session() 获取此单例来读写状态。
"""

import threading
import queue
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Dict, Any


@dataclass
class Item:
    """背包物品"""
    item_id: str
    name: str
    quantity: int
    can_disassemble: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "name": self.name,
            "quantity": self.quantity,
            "can_disassemble": self.can_disassemble,
        }


@dataclass
class RoleInfo:
    """角色信息"""
    role_id: str
    role_name: str
    role_job: str
    role_index: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role_id": self.role_id,
            "role_name": self.role_name,
            "role_job": self.role_job,
            "role_index": self.role_index,
        }


class GameSession:
    """游戏会话单例，线程安全。"""

    def __init__(self):
        self._lock = threading.Lock()

        # ---- 连接状态 ----
        self.sock = None                          # 当前游戏服 TCP socket
        self.session_id: Optional[str] = None    # 登录后取得的 hex session
        self.connected: bool = False
        self.connection_status: str = "disconnected"  # disconnected / connecting / connected / error

        # ---- 账号 / 服务器 / 角色 ----
        self.account: Optional[str] = None
        self.server_name: Optional[str] = None
        self.server_ip: Optional[str] = None
        self.server_port: Optional[int] = None
        self.current_role: Optional[RoleInfo] = None
        self.available_roles: list = []           # [RoleInfo, ...]
        self.announcement: str = ""               # 登录后获取的公告文本
        self.server_list: list = []               # 从登录响应解析的服务器列表

        # ---- 背包 ----
        self.backpack_items: Dict[str, Item] = {}  # item_id -> Item

        # ---- 角色属性 ----
        self.role_stats: Dict[str, str] = {}       # {'力量': '983', ...}

        # ---- 心跳检测 ----
        self.last_recv_ts: float = 0.0             # 最近一次收到 DN 包的时间戳

        # ---- 报文日志（环形缓冲，最新 PACKET_LOG_MAX 条） ----
        from config import PACKET_LOG_MAX
        self._packet_log: deque = deque(maxlen=PACKET_LOG_MAX)

        # ---- SSE 事件队列（每个 SSE 连接订阅一个队列） ----
        self._sse_subscribers: list = []
        self._sse_lock = threading.Lock()

        # ---- 发送队列 & 工作线程 ----
        self.send_queue: queue.PriorityQueue = queue.PriorityQueue()
        self._send_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    #  背包操作                                                             #
    # ------------------------------------------------------------------ #
    def update_item(self, item: Item):
        with self._lock:
            self.backpack_items[item.item_id] = item

    def remove_item(self, item_id: str):
        with self._lock:
            self.backpack_items.pop(item_id, None)

    def consume_item(self, item_id: str, quantity: int = 1):
        """
        乐观消耗物品：校验后将数量减少 quantity，减至 0 时从背包移除。
        返回 (success: bool, error_msg: str)。
        此方法是原子操作（持锁期间完成校验与修改），防止超量使用或使用不存在的物品。
        """
        with self._lock:
            item = self.backpack_items.get(item_id)
            if not item:
                return False, "背包中不存在该物品"
            if item.quantity < quantity:
                return False, f"数量不足（当前 {item.quantity}，需要 {quantity}）"
            new_qty = item.quantity - quantity
            if new_qty == 0:
                del self.backpack_items[item_id]
            else:
                item.quantity = new_qty
            return True, ""

    def get_backpack_list(self) -> list:
        with self._lock:
            return [item.to_dict() for item in self.backpack_items.values()]

    # ------------------------------------------------------------------ #
    #  报文日志                                                             #
    # ------------------------------------------------------------------ #
    def append_packet(self, record: dict):
        """追加一条报文记录（PacketRecord.to_dict()）。"""
        with self._lock:
            self._packet_log.append(record)
        self._notify_sse("packet", record)

    def get_packet_log(self, limit: int = 100,
                       direction: Optional[str] = None,
                       parsed_only: Optional[bool] = None,
                       annotated_only: Optional[bool] = None) -> list:
        """返回满足过滤条件的最新 limit 条报文记录。"""
        with self._lock:
            records = list(self._packet_log)

        if direction:
            records = [r for r in records if r.get("direction") == direction.upper()]
        if parsed_only is True:
            records = [r for r in records if r.get("parsed") is not None]
        elif parsed_only is False:
            records = [r for r in records if r.get("parsed") is None]
        if annotated_only is True:
            records = [r for r in records if r.get("annotation")]

        return records[-limit:]

    # ------------------------------------------------------------------ #
    #  SSE 事件广播                                                         #
    # ------------------------------------------------------------------ #
    def subscribe_sse(self) -> queue.Queue:
        """注册一个 SSE 订阅者，返回其专属事件队列。"""
        q: queue.Queue = queue.Queue(maxsize=200)
        with self._sse_lock:
            self._sse_subscribers.append(q)
        return q

    def unsubscribe_sse(self, q: queue.Queue):
        with self._sse_lock:
            try:
                self._sse_subscribers.remove(q)
            except ValueError:
                pass

    def _notify_sse(self, event_type: str, data: Any):
        """向所有 SSE 订阅者广播事件，丢弃满队列的慢消费者。"""
        import json
        payload = json.dumps({"type": event_type, "data": data}, ensure_ascii=False)
        with self._sse_lock:
            dead = []
            for q in self._sse_subscribers:
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._sse_subscribers.remove(q)

    def notify_backpack_update(self):
        """背包变更后广播一次完整背包列表。"""
        self._notify_sse("backpack", self.get_backpack_list())

    def notify_status_change(self):
        """连接状态变更后广播。"""
        self._notify_sse("status", self.get_status())

    # ------------------------------------------------------------------ #
    #  状态查询                                                             #
    # ------------------------------------------------------------------ #
    def get_status(self) -> dict:
        import time
        last_recv_age = round(time.time() - self.last_recv_ts, 1) if self.last_recv_ts > 0 else None
        return {
            "connected": self.connected,
            "connection_status": self.connection_status,
            "account": self.account,
            "server_name": self.server_name,
            "role": self.current_role.to_dict() if self.current_role else None,
            "backpack_count": len(self.backpack_items),
            "last_recv_age": last_recv_age,   # 距上次收到 DN 包的秒数，None 表示从未收到
        }

    def reset(self):
        """断开连接，清理状态（保留 packet_log 和 annotations）。"""
        with self._lock:
            if self.sock:
                try:
                    self.sock.close()
                except Exception:
                    pass
                self.sock = None
            self.connected = False
            self.connection_status = "disconnected"
            self.session_id = None
            self.current_role = None
            self.available_roles = []
            self.backpack_items = {}
            self.role_stats = {}
            self.last_recv_ts = 0.0
        self.notify_status_change()


# 模块级单例
_session: Optional[GameSession] = None
_session_lock = threading.Lock()


def get_session() -> GameSession:
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                _session = GameSession()
    return _session
