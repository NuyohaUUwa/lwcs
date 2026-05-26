"""运行时会话唯一实现。"""

import queue
import sys
import threading
from collections import deque
from typing import Any, Dict, Iterable, Optional

_CURRENT_MODULE = sys.modules[__name__]
if __name__ == "backend.runtime.session":
    sys.modules.setdefault("game_test.backend.runtime.session", _CURRENT_MODULE)
elif __name__ == "game_test.backend.runtime.session":
    sys.modules.setdefault("backend.runtime.session", _CURRENT_MODULE)

from game_test.backend.infrastructure.config import (
    DEFAULT_BATTLE_LOOP_DELAY_MS,
    DEFAULT_MAP_NPC_ID_HEX,
    DEFAULT_MAP_NPC_UTF8_TEXT,
    PACKET_LOG_MAX,
)

from .models import Item, RoleInfo


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
        self.available_roles: list[RoleInfo] = []
        self.announcement: str = ""
        self.server_list: list[dict[str, Any]] = []

        # ---- 背包 / 属性 ----
        self.backpack_items: Dict[str, Item] = {}
        self.role_stats: Dict[str, str] = {}
        self.role_stats_full_refresh_on_next_ed07: bool = False
        self.gold_reserve_copper: int = 0
        self.gold_safe_copper: int = 0
        self.gold_backpack_copper: int = 0
        self.auto_decompose_s1_enabled: bool = False
        self.auto_decompose_s1_pending: bool = False
        # 固定使用 config 默认 NPC；不再从包解析/外部接口动态切换。
        self.current_map_npc_id_hex: str = DEFAULT_MAP_NPC_ID_HEX
        self.current_map_npc_utf8_text: str = DEFAULT_MAP_NPC_UTF8_TEXT

        # ---- 心跳检测 ----
        self.last_recv_ts: float = 0.0

        # ---- TCP 收包拼帧（多条报文一次 recv / 半包跨 recv）----
        self.recv_framing_buffer: bytes = b""

        # ---- 战斗运行时 ----
        self.battle_state: str = "idle"
        self.battle_in_progress: bool = False
        self.battle_current_monster: str = ""
        self.battle_last_action: str = ""
        self.battle_last_response_ts: float = 0.0
        self.battle_round_seq: int = 0
        self.battle_last_result: Dict[str, Any] = {}
        self.battle_loop_running: bool = False
        self.battle_loop_delay_ms: int = DEFAULT_BATTLE_LOOP_DELAY_MS
        self.battle_loop_timer = None
        self.battle_loop_monster_code: str = ""
        self.battle_wait_deadline_ts: float = 0.0
        self.battle_next_start_ts: float = 0.0
        self.battle_total_count: int = 0
        self.battle_total_exp: int = 0
        self.battle_total_gold_copper: int = 0
        self.auto_use_pending_actions: list[dict[str, Any]] = []
        self.battle_preflight_teleport_used_once: bool = False
        self.battle_f703_timeout_recover_count: int = 0

        # ---- 后端控制平面 ----
        self.auto_reconnect_enabled: bool = False
        self.reconnect_state: str = "idle"
        self.reconnect_reason: str = ""
        self.reconnect_attempts: int = 0
        self.reconnect_max_attempts: int = 0
        self.reconnect_last_error: str = ""
        self.reconnect_next_retry_ts: float = 0.0
        self.reconnect_banned_until_ts: float = 0.0
        self.control_thread: Optional[threading.Thread] = None

        # ---- 报文日志 ----
        self._packet_log: deque[dict[str, Any]] = deque(maxlen=PACKET_LOG_MAX)

        # ---- SSE 订阅 ----
        self._sse_subscribers: list[queue.Queue[str]] = []
        self._sse_lock = threading.Lock()

        # ---- 发送运行时 ----
        self.send_queue: queue.PriorityQueue[tuple[int, str]] = queue.PriorityQueue()
        self._send_lock = threading.RLock()

    def update_item(self, item: Item):
        with self._lock:
            self.backpack_items[item.item_id] = item

    def set_current_map_npc(self, id_hex: str, utf8_text: str = "") -> None:
        """写入当前地图 NPC（4 字节 id 的 8 位 hex）；非法长度则忽略。"""
        h = str(id_hex or "").strip().lower()
        if len(h) != 8:
            return
        try:
            int(h, 16)
        except ValueError:
            return
        with self._lock:
            self.current_map_npc_id_hex = h
            self.current_map_npc_utf8_text = str(utf8_text or "").strip()

    def replace_backpack_items(self, items: Dict[str, Item]) -> None:
        """以权威快照整体替换背包（如 d607 全量列表）。"""
        with self._lock:
            self.backpack_items = dict(items)

    def apply_optimistic_obtain_items(self, items: Iterable[Item], *, delta_sign: int = 1) -> bool:
        """
        乐观背包增减（非 d607 场景）。

        规则：
        - `delta_sign=+1`：把 `item.quantity` 当作“获得数量”，对当前背包数量做累加
        - `delta_sign=-1`：把 `item.quantity` 当作“失去数量”，对当前背包数量做扣减；扣到 <=0 则移除

        只有 d607（权威全量列表）才会通过 `replace_backpack_items` 覆盖整个背包。
        """
        delta_sign = 1 if delta_sign >= 0 else -1
        changed = False
        with self._lock:
            for item in items:
                # 数量字段来自报文解析，理论上是“增减幅度”的无符号值；这里按 delta_sign 转成增减。
                delta_mag = int(item.quantity or 0)
                if delta_mag == 0:
                    continue

                if delta_sign > 0:
                    existing = self.backpack_items.get(item.item_id)
                    if existing:
                        existing.quantity += delta_mag
                    else:
                        item.quantity = delta_mag
                        self.backpack_items[item.item_id] = item
                    changed = True
                else:
                    existing = self.backpack_items.get(item.item_id)
                    if not existing:
                        continue
                    new_qty = existing.quantity - delta_mag
                    if new_qty > 0:
                        existing.quantity = new_qty
                    else:
                        del self.backpack_items[item.item_id]
                    changed = True
        return changed

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

    def get_backpack_list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [item.to_dict() for item in self.backpack_items.values()]

    def update_gold(self, values: dict[str, int]) -> None:
        with self._lock:
            if "reserve" in values:
                self.gold_reserve_copper = int(values["reserve"] or 0)
            if "safe" in values:
                self.gold_safe_copper = int(values["safe"] or 0)
            if "backpack" in values:
                self.gold_backpack_copper = int(values["backpack"] or 0)

    def clear_gold(self) -> None:
        with self._lock:
            self.gold_reserve_copper = 0
            self.gold_safe_copper = 0
            self.gold_backpack_copper = 0

    def get_gold_snapshot(self) -> dict[str, Any]:
        from game_test.backend.domain.inventory.gold import gold_snapshot_from_values

        with self._lock:
            values = {
                "reserve": self.gold_reserve_copper,
                "safe": self.gold_safe_copper,
                "backpack": self.gold_backpack_copper,
            }
        return gold_snapshot_from_values(values)

    def append_packet(self, record: dict[str, Any]):
        with self._lock:
            self._packet_log.append(record)
        self._notify_sse("packet", record)

    def clear_packet_log(self) -> int:
        with self._lock:
            cleared = len(self._packet_log)
            self._packet_log.clear()
        return cleared

    def get_packet_log(
        self,
        limit: int = 100,
        direction: Optional[str] = None,
        parsed_only: Optional[bool] = None,
        annotated_only: Optional[bool] = None,
    ) -> list[dict[str, Any]]:
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

    def subscribe_sse(self) -> queue.Queue[str]:
        q: queue.Queue[str] = queue.Queue(maxsize=200)
        with self._sse_lock:
            self._sse_subscribers.append(q)
        return q

    def unsubscribe_sse(self, q: queue.Queue[str]):
        with self._sse_lock:
            try:
                self._sse_subscribers.remove(q)
            except ValueError:
                pass

    def _notify_sse(self, event_type: str, data: Any):
        import json

        payload = json.dumps({"type": event_type, "data": data}, ensure_ascii=False)
        with self._sse_lock:
            try:
                subscribers = list(self._sse_subscribers)
            except Exception:
                return
            dead = []
            for q in subscribers:
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                try:
                    self._sse_subscribers.remove(q)
                except (ValueError, KeyError):
                    pass

    def notify_backpack_update(self):
        self._notify_sse("backpack", self.get_backpack_list())

    def notify_gold_update(self):
        self._notify_sse("gold", self.get_gold_snapshot())

    def notify_synthesis_result(self, payload: dict[str, Any]) -> None:
        """合成结果（e80301004f51），供前端在背包区展示。"""
        self._notify_sse("synthesis_result", dict(payload))

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
                "last_response_ts": self.battle_last_response_ts,
                "round_seq": self.battle_round_seq,
                "last_result": dict(self.battle_last_result),
                "loop_running": self.battle_loop_running,
                "loop_monster_code": self.battle_loop_monster_code,
                "loop_delay_ms": self.battle_loop_delay_ms,
                "wait_deadline_ts": self.battle_wait_deadline_ts,
                "next_start_ts": self.battle_next_start_ts,
                "total_count": self.battle_total_count,
                "total_exp": self.battle_total_exp,
                "total_gold_copper": self.battle_total_gold_copper,
                "f703_timeout_recover_count": self.battle_f703_timeout_recover_count,
            },
        )

    def get_control_state(self) -> dict[str, Any]:
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
                self.account
                and self.login_password
                and self.login_server_name
                and self.server_ip
                and self.server_port
                and self.reconnect_role_id
            ),
        }

    def notify_control_state(self):
        self._notify_sse("control_state", self.get_control_state())

    def stop_runtime(self):
        stop_event = self.connection_stop_event
        if stop_event:
            stop_event.set()

    def clear_connection_runtime(self):
        self.connection_stop_event = None
        self.recv_thread = None
        self.send_thread = None
        self.heartbeat_thread = None
        self.recv_framing_buffer = b""

    def discard_send_queue(self):
        """丢弃待发报文队列。断线后须清空，否则重连的发包线程会继续发出旧连接上入队的 f703 等。"""
        self.send_queue = queue.PriorityQueue()

    def get_status(self) -> dict[str, Any]:
        import time

        last_recv_age = round(time.time() - self.last_recv_ts, 1) if self.last_recv_ts > 0 else None
        with self._lock:
            npc_id = self.current_map_npc_id_hex
            npc_name = self.current_map_npc_utf8_text
        current_map_npc = (
            {"id_hex": npc_id, "utf8_text": npc_name}
            if npc_id and len(npc_id) == 8
            else None
        )
        return {
            "connected": self.connected,
            "connection_status": self.connection_status,
            "account": self.account,
            "server_name": self.server_name,
            "role": self.current_role.to_dict() if self.current_role else None,
            "backpack_count": len(self.backpack_items),
            "auto_decompose_s1_enabled": self.auto_decompose_s1_enabled,
            "last_recv_age": last_recv_age,
            "default_battle_loop_delay_ms": DEFAULT_BATTLE_LOOP_DELAY_MS,
            "current_map_npc": current_map_npc,
            "control_state": self.get_control_state(),
            "battle_state": {
                "state": self.battle_state,
                "in_progress": self.battle_in_progress,
                "current_monster": self.battle_current_monster,
                "last_action": self.battle_last_action,
                "last_response_ts": self.battle_last_response_ts,
                "round_seq": self.battle_round_seq,
                "last_result": dict(self.battle_last_result),
                "loop_running": self.battle_loop_running,
                "loop_monster_code": self.battle_loop_monster_code,
                "loop_delay_ms": self.battle_loop_delay_ms,
                "wait_deadline_ts": self.battle_wait_deadline_ts,
                "next_start_ts": self.battle_next_start_ts,
                "total_count": self.battle_total_count,
                "total_exp": self.battle_total_exp,
                "total_gold_copper": self.battle_total_gold_copper,
                "f703_timeout_recover_count": self.battle_f703_timeout_recover_count,
            },
        }

    def reset(self):
        """断开连接，清理状态；保留 packet log 与标注数据。"""
        from game_test.core.connector import close_connection

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
            self.role_stats_full_refresh_on_next_ed07 = False
            self.gold_reserve_copper = 0
            self.gold_safe_copper = 0
            self.gold_backpack_copper = 0
            self.auto_decompose_s1_enabled = False
            self.auto_decompose_s1_pending = False
            self.current_map_npc_id_hex = DEFAULT_MAP_NPC_ID_HEX
            self.current_map_npc_utf8_text = DEFAULT_MAP_NPC_UTF8_TEXT
            self.last_recv_ts = 0.0
            self.recv_framing_buffer = b""
            self.battle_state = "idle"
            self.battle_in_progress = False
            self.battle_current_monster = ""
            self.battle_last_action = ""
            self.battle_last_response_ts = 0.0
            self.battle_round_seq = 0
            self.battle_last_result = {}
            self.battle_loop_running = False
            self.battle_loop_monster_code = ""
            self.battle_loop_delay_ms = DEFAULT_BATTLE_LOOP_DELAY_MS
            self.battle_loop_timer = None
            self.battle_wait_deadline_ts = 0.0
            self.battle_next_start_ts = 0.0
            self.battle_total_count = 0
            self.battle_total_exp = 0
            self.battle_total_gold_copper = 0
            self.auto_use_pending_actions = []
            self.battle_preflight_teleport_used_once = False
            self.battle_f703_timeout_recover_count = 0
            self.reconnect_state = "idle"
            self.reconnect_reason = ""
            self.reconnect_attempts = 0
            self.reconnect_last_error = ""
            self.reconnect_next_retry_ts = 0.0
            self.reconnect_banned_until_ts = 0.0
            self.clear_connection_runtime()
        self.discard_send_queue()
        self.notify_status_change()
        self.notify_battle_state()
        self.notify_control_state()
        self.notify_gold_update()


_session: Optional[GameSession] = None
_session_lock = threading.Lock()


def get_session() -> GameSession:
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                _session = GameSession()
    return _session


__all__ = ["GameSession", "Item", "RoleInfo", "get_session"]
