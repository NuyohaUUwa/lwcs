"""
全局游戏会话单例。
仅维护状态、缓存、连接运行时句柄与 SSE 广播能力，不承载业务流程。
"""

import queue
import threading
from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class Item:
    """背包物品。"""

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
    """角色信息。"""

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
        self.sock = None
        self.session_id: Optional[str] = None
        self.connected: bool = False
        self.connection_status: str = "disconnected"
        self.connection_stop_event: Optional[threading.Event] = None
        self.recv_thread: Optional[threading.Thread] = None
        self.send_thread: Optional[threading.Thread] = None
        self.heartbeat_thread: Optional[threading.Thread] = None

        # ---- 账号 / 服务器 / 角色 ----
        self.account: Optional[str] = None
        self.login_password: Optional[str] = None
        self.login_server_name: Optional[str] = None
        self.server_name: Optional[str] = None
        self.server_ip: Optional[str] = None
        self.server_port: Optional[int] = None
        self.reconnect_role_id: Optional[str] = None
        self.current_role: Optional[RoleInfo] = None
        self.available_roles: list = []
        self.announcement: str = ""
        self.server_list: list = []

        # ---- 背包 / 属性 ----
        self.backpack_items: Dict[str, Item] = {}
        self.role_stats: Dict[str, str] = {}

        # ---- 心跳检测 ----
        self.last_recv_ts: float = 0.0

        # ---- 战斗运行时 ----
        self.battle_state: str = "idle"
        self.battle_in_progress: bool = False
        self.battle_current_monster: str = ""
        self.battle_last_action: str = ""
        self.battle_can_create_next: bool = False
        self.battle_last_response_ts: float = 0.0
        self.battle_round_seq: int = 0
        self.battle_last_result: Dict[str, Any] = {}
        self.battle_loop_running: bool = False
        self.battle_loop_delay_ms: int = 1900
        self.battle_loop_timer = None
        self.battle_mode: str = "idle"
        self.battle_loop_monster_code: str = ""
        self.battle_wait_deadline_ts: float = 0.0
        self.battle_next_start_ts: float = 0.0
        self.battle_total_count: int = 0
        self.battle_total_exp: int = 0
        self.battle_total_gold_copper: int = 0
        self.auto_use_pending_actions: list = []
        self.battle_preflight_teleport_used_once: bool = False

        # ---- 后端控制平面 ----
        self.auto_reconnect_enabled: bool = False
        self.reconnect_state: str = "idle"
        self.reconnect_reason: str = ""
        self.reconnect_attempts: int = 0
        self.reconnect_max_attempts: int = 3
        self.reconnect_last_error: str = ""
        self.reconnect_next_retry_ts: float = 0.0
        self.reconnect_banned_until_ts: float = 0.0
        self.control_thread: Optional[threading.Thread] = None

        # ---- 报文日志 ----
        from config import PACKET_LOG_MAX

        self._packet_log: deque = deque(maxlen=PACKET_LOG_MAX)

        # ---- SSE 订阅 ----
        self._sse_subscribers: list = []
        self._sse_lock = threading.Lock()

        # ---- 发送运行时 ----
        self.send_queue: queue.PriorityQueue = queue.PriorityQueue()
        self._send_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    #  背包操作                                                            #
    # ------------------------------------------------------------------ #
    def update_item(self, item: Item):
        with self._lock:
            self.backpack_items[item.item_id] = item

    def remove_item(self, item_id: str):
        with self._lock:
            self.backpack_items.pop(item_id, None)

    def consume_item(self, item_id: str, quantity: int = 1):
        """乐观消耗物品。"""
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
    #  报文日志                                                            #
    # ------------------------------------------------------------------ #
    def append_packet(self, record: dict):
        with self._lock:
            self._packet_log.append(record)
        self._notify_sse("packet", record)

    def get_packet_log(
        self,
        limit: int = 100,
        direction: Optional[str] = None,
        parsed_only: Optional[bool] = None,
        annotated_only: Optional[bool] = None,
    ) -> list:
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
    #  SSE 事件广播                                                        #
    # ------------------------------------------------------------------ #
    def subscribe_sse(self) -> queue.Queue:
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
        self._notify_sse("backpack", self.get_backpack_list())

    def notify_status_change(self):
        self._notify_sse("status", self.get_status())

    def notify_battle_state(self):
        self._notify_sse(
            "battle_state",
            {
                "state": self.battle_state,
                "in_progress": self.battle_in_progress,
                "current_monster": self.battle_current_monster,
                "last_action": self.battle_last_action,
                "can_create_next": self.battle_can_create_next,
                "last_response_ts": self.battle_last_response_ts,
                "round_seq": self.battle_round_seq,
                "last_result": dict(self.battle_last_result),
                "mode": self.battle_mode,
                "loop_running": self.battle_loop_running,
                "loop_monster_code": self.battle_loop_monster_code,
                "loop_delay_ms": self.battle_loop_delay_ms,
                "wait_deadline_ts": self.battle_wait_deadline_ts,
                "next_start_ts": self.battle_next_start_ts,
                "total_count": self.battle_total_count,
                "total_exp": self.battle_total_exp,
                "total_gold_copper": self.battle_total_gold_copper,
            },
        )

    def get_control_state(self) -> dict:
        import time

        now = time.time()
        next_retry_in = None
        if self.reconnect_next_retry_ts > 0:
            next_retry_in = max(0.0, round(self.reconnect_next_retry_ts - now, 1))
        banned_wait_in = None
        if self.reconnect_banned_until_ts > 0:
            banned_wait_in = max(0.0, round(self.reconnect_banned_until_ts - now, 1))
        return {
            "auto_reconnect_enabled": self.auto_reconnect_enabled,
            "reconnect_state": self.reconnect_state,
            "reconnect_reason": self.reconnect_reason,
            "reconnect_attempts": self.reconnect_attempts,
            "reconnect_max_attempts": self.reconnect_max_attempts,
            "reconnect_last_error": self.reconnect_last_error,
            "reconnect_next_retry_ts": self.reconnect_next_retry_ts,
            "reconnect_next_retry_in": next_retry_in,
            "reconnect_banned_until_ts": self.reconnect_banned_until_ts,
            "reconnect_banned_wait_in": banned_wait_in,
            "has_reconnect_context": bool(
                self.account and self.login_password and self.login_server_name and self.server_ip and self.server_port and self.reconnect_role_id
            ),
        }

    def notify_control_state(self):
        self._notify_sse("control_state", self.get_control_state())

    # ------------------------------------------------------------------ #
    #  运行时清理                                                          #
    # ------------------------------------------------------------------ #
    def stop_runtime(self):
        stop_event = self.connection_stop_event
        if stop_event:
            stop_event.set()

    def clear_connection_runtime(self):
        self.connection_stop_event = None
        self.recv_thread = None
        self.send_thread = None
        self.heartbeat_thread = None

    # ------------------------------------------------------------------ #
    #  状态查询                                                            #
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
            "last_recv_age": last_recv_age,
            "control_state": self.get_control_state(),
            "battle_state": {
                "state": self.battle_state,
                "in_progress": self.battle_in_progress,
                "current_monster": self.battle_current_monster,
                "last_action": self.battle_last_action,
                "can_create_next": self.battle_can_create_next,
                "last_response_ts": self.battle_last_response_ts,
                "round_seq": self.battle_round_seq,
                "last_result": dict(self.battle_last_result),
                "mode": self.battle_mode,
                "loop_running": self.battle_loop_running,
                "loop_monster_code": self.battle_loop_monster_code,
                "loop_delay_ms": self.battle_loop_delay_ms,
                "wait_deadline_ts": self.battle_wait_deadline_ts,
                "next_start_ts": self.battle_next_start_ts,
                "total_count": self.battle_total_count,
                "total_exp": self.battle_total_exp,
                "total_gold_copper": self.battle_total_gold_copper,
            },
        }

    def reset(self):
        """断开连接，清理状态；保留 packet log 与标注数据。"""
        from core.connector import close_connection

        self.stop_runtime()
        close_connection()
        with self._lock:
            self.connected = False
            self.connection_status = "disconnected"
            self.session_id = None
            self.account = None
            self.login_password = None
            self.login_server_name = None
            self.server_name = None
            self.server_ip = None
            self.server_port = None
            self.reconnect_role_id = None
            self.current_role = None
            self.available_roles = []
            self.backpack_items = {}
            self.role_stats = {}
            self.last_recv_ts = 0.0
            self.battle_state = "idle"
            self.battle_in_progress = False
            self.battle_current_monster = ""
            self.battle_last_action = ""
            self.battle_can_create_next = False
            self.battle_last_response_ts = 0.0
            self.battle_round_seq = 0
            self.battle_last_result = {}
            self.battle_mode = "idle"
            self.battle_loop_running = False
            self.battle_loop_monster_code = ""
            self.battle_loop_delay_ms = 1900
            self.battle_loop_timer = None
            self.battle_wait_deadline_ts = 0.0
            self.battle_next_start_ts = 0.0
            self.battle_total_count = 0
            self.battle_total_exp = 0
            self.battle_total_gold_copper = 0
            self.auto_use_pending_actions = []
            self.battle_preflight_teleport_used_once = False
            self.reconnect_state = "idle"
            self.reconnect_reason = ""
            self.reconnect_attempts = 0
            self.reconnect_last_error = ""
            self.reconnect_next_retry_ts = 0.0
            self.reconnect_banned_until_ts = 0.0
            self.clear_connection_runtime()
        self.notify_status_change()
        self.notify_battle_state()
        self.notify_control_state()


_session: Optional[GameSession] = None
_session_lock = threading.Lock()


def get_session() -> GameSession:
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                _session = GameSession()
    return _session
