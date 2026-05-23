"""小号独立登录、收包与重连运行期。"""

from __future__ import annotations

import binascii
import random
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from game_test.backend.domain.auth.login import build_login_packet, parse_login_response
from game_test.backend.domain.ladder.ladder import build_small_battle_ack_packet, build_team_accept_packet
from game_test.backend.domain.roles.roles import (
    build_enter_game_extra_packet,
    build_role_list_packet,
    build_select_role_packet,
    parse_role_data,
    parse_select_role_response,
)
from game_test.backend.infrastructure.config import LOGIN_SERVERS, RECV_BUFSIZE
from game_test.backend.infrastructure.data_manager import update_small_account_last_user_id
from game_test.backend.runtime import get_session
from game_test.core.codec import extract_utf8_segments, split_game_frame_bytes

_IGNORED_FINGERPRINTS = {
    "e8030100514f",
    "e8030100d607",
    "e80301004d4f",
    "e80301001d57",
    "e8030100f207",
    "e8030100ed07",
    "e8030100e107",
    "e8030100e207",
}
_BATTLE_LIST_FINGERPRINTS = {"e8030100de07", "e8030100df07", "e8030100e407"}
_TEAM_INVITE_FINGERPRINT = "e8030100fb07"
_TEAM_STATE_FINGERPRINTS = {"e8030100fd07", "e8030100fc07"}
_DAILY_CHECKIN_PACKET_HEX = "20000000e80313007d2e08f4f505882e00000e0000000c00636865636b496e446f3f7b7d"


def _send_all(sock: socket.socket, data: bytes) -> int:
    total = 0
    while total < len(data):
        sent = sock.send(data[total:])
        if sent <= 0:
            raise ConnectionError("socket 发送失败")
        total += sent
    return total


def _send_hex(sock: socket.socket, packet_hex: str) -> int:
    return _send_all(sock, binascii.unhexlify(packet_hex))


def _recv_frames(sock: socket.socket, buffer: bytes, *, timeout: float) -> tuple[list[bytes], bytes]:
    old_timeout = sock.gettimeout()
    deadline = time.monotonic() + max(0.1, timeout)
    frames: list[bytes] = []
    rest = buffer
    try:
        while time.monotonic() < deadline:
            sock.settimeout(max(0.05, min(1.0, deadline - time.monotonic())))
            try:
                chunk = sock.recv(RECV_BUFSIZE)
            except socket.timeout:
                continue
            if not chunk:
                raise ConnectionResetError("服务器主动断开连接")
            parsed, rest = split_game_frame_bytes(rest + chunk)
            frames.extend(parsed)
            if frames:
                return frames, rest
        return frames, rest
    finally:
        try:
            sock.settimeout(old_timeout)
        except Exception:
            pass


@dataclass
class SmallAccountRuntime:
    account: str
    password: str
    last_user_id: str = ""
    status: str = "offline"
    role_id: str = ""
    role_name: str = ""
    role_index: int = 0
    error: str = ""
    reconnecting: bool = False
    expected_online: bool = False
    last_recv_ts: float = 0.0
    last_ack_ts: float = 0.0
    team_joined: bool = False
    checkin_sent: bool = False
    sock: socket.socket | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    recv_thread: threading.Thread | None = None
    worker_thread: threading.Thread | None = None
    recv_buffer: bytes = b""
    send_lock: threading.RLock = field(default_factory=threading.RLock)

    def to_dict(self) -> dict[str, Any]:
        return {
            "account": self.account,
            "status": self.status,
            "role_id": self.role_id,
            "role_name": self.role_name,
            "role_index": self.role_index,
            "last_user_id": self.last_user_id,
            "error": self.error,
            "reconnecting": self.reconnecting,
            "expected_online": self.expected_online,
            "team_joined": self.team_joined,
            "checkin_sent": self.checkin_sent,
            "last_recv_age": round(time.time() - self.last_recv_ts, 1) if self.last_recv_ts else None,
        }


class SmallAccountManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, SmallAccountRuntime] = {}

    def status(self) -> list[dict[str, Any]]:
        with self._lock:
            return [runtime.to_dict() for runtime in self._sessions.values()]

    def online_user_ids(self) -> list[str]:
        with self._lock:
            return [
                runtime.role_id
                for runtime in self._sessions.values()
                if runtime.status == "online" and runtime.role_id
            ]

    @staticmethod
    def _runtime_matches_packet(
        runtime: SmallAccountRuntime,
        utf8_text: str,
        body_hex: str,
        body_ascii: str,
    ) -> bool:
        account = str(runtime.account or "").strip()
        if len(account) >= 2:
            acc_lower = account.lower()
            if acc_lower in body_ascii or acc_lower in utf8_text.lower():
                return True
            try:
                if account.isascii() and account.encode("ascii").hex().lower() in body_hex:
                    return True
            except (UnicodeEncodeError, ValueError):
                pass
        role_name = str(runtime.role_name or "").strip()
        if role_name and role_name in utf8_text:
            return True
        role_id = str(runtime.role_id or "").strip().lower()
        if role_id and (role_id in utf8_text.lower() or role_id in body_hex):
            return True
        return False

    def mark_joined_from_packet(self, packet_hex: str, fingerprint: str) -> list[dict[str, str]]:
        fp = str(fingerprint or "").strip().lower()
        utf8_text = extract_utf8_segments(packet_hex)
        body_hex = packet_hex[16:].lower() if len(packet_hex) > 16 else ""
        try:
            body_ascii = bytes.fromhex(body_hex).decode("latin-1", errors="ignore").lower()
        except (ValueError, binascii.Error):
            body_ascii = ""

        if fp == "e8030100fd07" and "加入队伍" not in utf8_text:
            return []

        matched = []
        with self._lock:
            runtimes = list(self._sessions.values())
        for runtime in runtimes:
            if not runtime.expected_online and runtime.status in ("stopped", "offline"):
                continue
            if not self._runtime_matches_packet(runtime, utf8_text, body_hex, body_ascii):
                continue
            runtime.team_joined = True
            matched.append(
                {"account": runtime.account, "role_id": runtime.role_id, "role_name": runtime.role_name}
            )
        return matched

    def mark_joined_from_text(self, text: str) -> list[dict[str, str]]:
        body = str(text or "")
        if "加入队伍" not in body:
            return []
        matched = []
        with self._lock:
            runtimes = list(self._sessions.values())
        for runtime in runtimes:
            if not runtime.expected_online and runtime.status in ("stopped", "offline"):
                continue
            if self._runtime_matches_packet(runtime, body, "", body.lower()):
                runtime.team_joined = True
                matched.append(
                    {"account": runtime.account, "role_id": runtime.role_id, "role_name": runtime.role_name}
                )
        return matched

    def snapshot_target_accounts(self) -> list[str]:
        with self._lock:
            return [
                runtime.account
                for runtime in self._sessions.values()
                if runtime.expected_online and runtime.status not in ("stopped", "offline")
            ]

    def any_target_unhealthy(self, accounts: list[str]) -> bool:
        targets = {str(a).strip() for a in accounts if str(a).strip()}
        if not targets:
            return False
        with self._lock:
            for account in targets:
                runtime = self._sessions.get(account)
                if not runtime or not runtime.expected_online:
                    return True
                if runtime.status in ("reconnecting", "offline", "stopped"):
                    return True
                if runtime.error and runtime.status != "online":
                    return True
        return False

    def stop_targets(self, accounts: list[str]) -> dict[str, Any]:
        targets = [str(a).strip() for a in accounts if str(a).strip()]
        for account in targets:
            self.stop_one(account)
        return {"ok": True, "items": self.status()}

    def start_targets(
        self,
        accounts: list[str],
        config_items: list[dict[str, Any]],
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        by_account = {str(x.get("account", "")).strip(): x for x in config_items}
        started = []
        missing = []
        with self._lock:
            for account in [str(a).strip() for a in accounts if str(a).strip()]:
                item = by_account.get(account)
                if not item:
                    missing.append(account)
                    continue
                name = self._start_one_locked(item, force=force)
                if name:
                    started.append(name)
        if missing:
            return {"ok": False, "error": f"小号配置不存在：{', '.join(missing)}", "missing": missing}
        return {"ok": True, "started": started, "items": self.status()}

    def wait_targets_online(
        self,
        accounts: list[str],
        timeout_s: float,
        *,
        stop_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        targets = [str(a).strip() for a in accounts if str(a).strip()]
        deadline = time.time() + max(1.0, timeout_s)
        pending = list(targets)
        while time.time() < deadline:
            if stop_event and stop_event.is_set():
                return {"ok": False, "stopped": True, "error": "已停止"}
            pending = []
            with self._lock:
                for account in targets:
                    runtime = self._sessions.get(account)
                    if not runtime or runtime.status != "online" or not runtime.sock:
                        pending.append(account)
            if not pending:
                return {"ok": True}
            if stop_event:
                stop_event.wait(0.5)
            else:
                time.sleep(0.5)
        return {"ok": False, "error": f"小号上线超时：{', '.join(pending)}", "pending": pending}

    def wait_targets_joined(
        self,
        accounts: list[str],
        timeout_s: float,
        *,
        stop_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        targets = [str(a).strip() for a in accounts if str(a).strip()]
        deadline = time.time() + max(1.0, timeout_s)
        missing = list(targets)
        while time.time() < deadline:
            if stop_event and stop_event.is_set():
                return {"ok": False, "stopped": True, "error": "已停止"}
            missing = []
            with self._lock:
                for account in targets:
                    runtime = self._sessions.get(account)
                    if not runtime or not runtime.team_joined:
                        missing.append(account)
            if not missing:
                return {"ok": True}
            if stop_event:
                stop_event.wait(0.5)
            else:
                time.sleep(0.5)
        details = []
        with self._lock:
            for account in missing:
                runtime = self._sessions.get(account)
                if runtime:
                    details.append(f"{account}({runtime.role_name or runtime.role_id or '未选角'})")
                else:
                    details.append(account)
        return {"ok": False, "error": f"小号入队超时：{', '.join(details)}", "missing": missing}

    def reset_team_joined_for_targets(self, accounts: list[str]) -> None:
        targets = {str(a).strip() for a in accounts if str(a).strip()}
        with self._lock:
            for runtime in self._sessions.values():
                if runtime.account in targets:
                    runtime.team_joined = False

    def start_first_two(self, accounts: list[dict[str, Any]]) -> dict[str, Any]:
        selected = accounts[:2]
        if not selected:
            return {"ok": False, "error": "当前主号没有配置小号"}
        started = []
        with self._lock:
            for item in selected:
                account = self._start_one_locked(item)
                if account:
                    started.append(account)
        return {"ok": True, "started": started, "items": self.status()}

    def start_one(self, account_item: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            account = self._start_one_locked(account_item)
        if not account:
            return {"ok": False, "error": "小号账号或密码为空"}
        return {"ok": True, "started": [account], "items": self.status()}

    def _start_one_locked(self, item: dict[str, Any], *, force: bool = False) -> str:
        account = str(item.get("account", "")).strip()
        password = str(item.get("password", "")).strip()
        if not account or not password:
            return ""
        existing = self._sessions.get(account)
        if (
            existing
            and not force
            and existing.expected_online
            and existing.status in ("online", "logging_in", "reconnecting")
        ):
            return account
        if existing:
            existing.expected_online = False
            existing.stop_event.set()
            self._close_runtime_socket(existing)
        runtime = SmallAccountRuntime(
            account=account,
            password=password,
            last_user_id=str(item.get("last_user_id", "")).strip().lower(),
            expected_online=True,
            status="logging_in",
        )
        self._sessions[account] = runtime
        thread = threading.Thread(target=self._login_worker, args=(runtime,), daemon=True, name=f"small-login-{account}")
        runtime.worker_thread = thread
        thread.start()
        return account

    def stop_all(self) -> dict[str, Any]:
        with self._lock:
            runtimes = list(self._sessions.values())
        for runtime in runtimes:
            runtime.expected_online = False
            runtime.stop_event.set()
            self._close_runtime_socket(runtime)
            runtime.status = "stopped"
            runtime.reconnecting = False
            runtime.error = ""
            runtime.last_recv_ts = 0.0
            runtime.team_joined = False
        return {"ok": True, "items": self.status()}

    def stop_one(self, account: str) -> dict[str, Any]:
        target = str(account or "").strip()
        if not target:
            return {"ok": False, "error": "小号账号不能为空"}
        with self._lock:
            runtime = self._sessions.get(target)
        if not runtime:
            return {"ok": True, "items": self.status()}
        runtime.expected_online = False
        runtime.stop_event.set()
        self._close_runtime_socket(runtime)
        runtime.status = "stopped"
        runtime.reconnecting = False
        runtime.error = ""
        runtime.last_recv_ts = 0.0
        runtime.team_joined = False
        return {"ok": True, "items": self.status()}

    def send_battle_ack_after_main(self, source: str = "") -> dict[str, Any]:
        """主号 f703 已发送后，按顺序让在线小号各发送一次 f703。"""
        with self._lock:
            runtimes = [
                runtime
                for runtime in self._sessions.values()
                if runtime.expected_online and runtime.status == "online" and runtime.sock
            ]
        sent = []
        failed = []
        for runtime in runtimes:
            try:
                packet_hex = build_small_battle_ack_packet()
                sock = runtime.sock
                if not sock:
                    continue
                self._send_runtime_hex(runtime, packet_hex)
                runtime.last_ack_ts = time.time()
                sent.append({"account": runtime.account, "role_id": runtime.role_id})
            except Exception as exc:
                failed.append({"account": runtime.account, "error": str(exc)})
                self._mark_runtime_disconnected(runtime, exc)
        return {"ok": True, "source": source, "sent": sent, "failed": failed}

    def _login_worker(self, runtime: SmallAccountRuntime) -> None:
        while runtime.expected_online and not runtime.stop_event.is_set():
            try:
                runtime.reconnecting = runtime.status == "reconnecting"
                runtime.status = "logging_in"
                runtime.error = ""
                runtime.team_joined = False
                runtime.checkin_sent = False
                self._perform_login(runtime)
                runtime.status = "online"
                runtime.reconnecting = False
                runtime.last_recv_ts = time.time()
                self._start_recv_loop(runtime)
                return
            except Exception as exc:
                if not runtime.expected_online or runtime.stop_event.is_set():
                    runtime.error = ""
                    runtime.status = "stopped"
                    runtime.reconnecting = False
                    runtime.last_recv_ts = 0.0
                else:
                    runtime.error = str(exc)
                    runtime.status = "reconnecting"
                    runtime.reconnecting = True
                self._close_runtime_socket(runtime)
                if not runtime.expected_online or runtime.stop_event.wait(1.0):
                    return

    def _perform_login(self, runtime: SmallAccountRuntime) -> None:
        main = get_session()
        with main._lock:
            login_server_name = main.login_server_name or main.server_name or "其他"
            server_ip = str(main.server_ip or "")
            server_port = int(main.server_port or 0)
            main_account = str(main.account or "")
            main_role_index = int(main.current_role.role_index if main.current_role else 0)
        if not server_ip or not server_port:
            raise RuntimeError("主号尚未选择游戏服")
        login_cfg = LOGIN_SERVERS.get(login_server_name) or LOGIN_SERVERS.get("其他")
        if not login_cfg:
            raise RuntimeError(f"未知登录服务器: {login_server_name}")

        login_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        login_sock.settimeout(15)
        try:
            login_sock.connect((str(login_cfg["ip"]), int(login_cfg["port"])))
            _send_hex(login_sock, build_login_packet(runtime.account, runtime.password))
            response = login_sock.recv(4096)
            if not response:
                raise RuntimeError("登录服无响应")
            parsed_login = parse_login_response(response)
            session_id = str(parsed_login["session_id"])
        finally:
            try:
                login_sock.close()
            except Exception:
                pass

        game_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        game_sock.settimeout(15)
        game_sock.connect((server_ip, server_port))
        runtime.sock = game_sock
        runtime.recv_buffer = b""

        _send_hex(game_sock, build_role_list_packet(session_id, server_ip, server_port))
        frames, rest = _recv_frames(game_sock, runtime.recv_buffer, timeout=5)
        runtime.recv_buffer = rest
        if not frames:
            raise RuntimeError("游戏服未返回角色列表")
        role_data = parse_role_data(b"".join(frames).hex())
        roles = list(role_data.get("userList", []))
        if not roles:
            raise RuntimeError("小号没有可选角色")
        selected = self._select_role(roles, runtime.last_user_id, main_role_index)
        role_id = str(selected.get("role_id", "")).strip().lower()
        _send_hex(game_sock, build_select_role_packet(role_id))
        frames, rest = _recv_frames(game_sock, runtime.recv_buffer, timeout=8)
        runtime.recv_buffer = rest
        parsed_select = parse_select_role_response(b"".join(frames))
        if not parsed_select.get("ok"):
            raise RuntimeError(f"小号选角失败: {parsed_select.get('text', '')}")
        time.sleep(0.8)
        _send_hex(game_sock, build_enter_game_extra_packet())

        runtime.role_id = role_id
        runtime.role_name = str(selected.get("role_name_cn", ""))
        runtime.role_index = int(selected.get("role_index", 0))
        runtime.last_user_id = role_id
        if main_account:
            update_small_account_last_user_id(main_account, runtime.account, role_id)
        self._send_daily_checkin(runtime)

    @staticmethod
    def _select_role(roles: list[dict[str, Any]], last_user_id: str, main_role_index: int) -> dict[str, Any]:
        clean_last = str(last_user_id or "").strip().lower()
        if clean_last:
            matched = next((r for r in roles if str(r.get("role_id", "")).lower() == clean_last), None)
            if matched:
                return matched
        matched = next((r for r in roles if int(r.get("role_index", -1)) == main_role_index), None)
        return matched or roles[0]

    def _start_recv_loop(self, runtime: SmallAccountRuntime) -> None:
        thread = threading.Thread(target=self._recv_loop, args=(runtime,), daemon=True, name=f"small-recv-{runtime.account}")
        runtime.recv_thread = thread
        thread.start()

    def _recv_loop(self, runtime: SmallAccountRuntime) -> None:
        sock = runtime.sock
        if not sock:
            return
        sock.settimeout(1.0)
        try:
            while runtime.expected_online and not runtime.stop_event.is_set():
                try:
                    chunk = sock.recv(RECV_BUFSIZE)
                    if not chunk:
                        raise ConnectionResetError("服务器主动断开连接")
                    frames, rest = split_game_frame_bytes(runtime.recv_buffer + chunk)
                    runtime.recv_buffer = rest
                    for frame in frames:
                        runtime.last_recv_ts = time.time()
                        self._handle_packet(runtime, frame)
                except socket.timeout:
                    continue
        except Exception as exc:
            self._close_runtime_socket(runtime)
            if runtime.expected_online and not runtime.stop_event.is_set():
                runtime.error = str(exc)
                runtime.status = "reconnecting"
                runtime.reconnecting = True
                runtime.stop_event.wait(1.0)
                if runtime.expected_online and not runtime.stop_event.is_set():
                    thread = threading.Thread(target=self._login_worker, args=(runtime,), daemon=True, name=f"small-relogin-{runtime.account}")
                    runtime.worker_thread = thread
                    thread.start()
            else:
                runtime.status = "stopped" if runtime.stop_event.is_set() else "offline"
                runtime.reconnecting = False
                runtime.error = ""
                runtime.last_recv_ts = 0.0
                runtime.team_joined = False

    def _handle_packet(self, runtime: SmallAccountRuntime, frame: bytes) -> None:
        packet_hex = frame.hex()
        fingerprint = packet_hex[8:20] if len(packet_hex) >= 20 else ""
        if fingerprint in _IGNORED_FINGERPRINTS:
            return
        if fingerprint in _TEAM_STATE_FINGERPRINTS:
            self.mark_joined_from_packet(packet_hex, fingerprint)
            return
        if fingerprint in _BATTLE_LIST_FINGERPRINTS:
            # 小号不再根据自己的下行包主动攻击；由主号 battle 流程在主号 f703 发送成功后调度。
            return
        if fingerprint == _TEAM_INVITE_FINGERPRINT:
            main = get_session()
            with main._lock:
                main_role_name = main.current_role.role_name if main.current_role else ""
            if main_role_name and main_role_name.encode("utf-8").hex() in packet_hex and runtime.sock:
                self._send_runtime_hex(runtime, build_team_accept_packet())

    def _mark_runtime_disconnected(self, runtime: SmallAccountRuntime, exc: Exception) -> None:
        self._close_runtime_socket(runtime)
        if runtime.expected_online and not runtime.stop_event.is_set():
            runtime.error = str(exc)
            runtime.status = "reconnecting"
            runtime.reconnecting = True
            thread = threading.Thread(target=self._login_worker, args=(runtime,), daemon=True, name=f"small-relogin-{runtime.account}")
            runtime.worker_thread = thread
            thread.start()
        else:
            runtime.status = "stopped" if runtime.stop_event.is_set() else "offline"
            runtime.reconnecting = False
            runtime.error = ""
            runtime.last_recv_ts = 0.0
            runtime.team_joined = False

    @staticmethod
    def _build_daily_checkin_packet() -> str:
        rand = random.randint(0, 0xFFFF).to_bytes(2, "little").hex()
        return _DAILY_CHECKIN_PACKET_HEX.replace("08f4", rand, 1)

    def _send_daily_checkin(self, runtime: SmallAccountRuntime) -> None:
        try:
            self._send_runtime_hex(runtime, self._build_daily_checkin_packet())
            runtime.checkin_sent = True
        except Exception as exc:
            runtime.error = f"小号每日签到发送失败: {exc}"

    @staticmethod
    def _send_runtime_hex(runtime: SmallAccountRuntime, packet_hex: str) -> int:
        sock = runtime.sock
        if not sock:
            raise ConnectionError("小号 socket 不可用")
        with runtime.send_lock:
            return _send_hex(sock, packet_hex)

    @staticmethod
    def _close_runtime_socket(runtime: SmallAccountRuntime) -> None:
        sock = runtime.sock
        runtime.sock = None
        if sock:
            try:
                sock.close()
            except Exception:
                pass


small_account_manager = SmallAccountManager()
