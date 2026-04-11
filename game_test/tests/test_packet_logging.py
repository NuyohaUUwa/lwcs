import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from core.connector import send_and_receive_once, send_packet, start_receive_loop
from core.session import get_session
from features.packet_probe import record_packet
from services import packet_log_service


class _FakeSendSocket:
    def __init__(self, sent_bytes: int | list[int]):
        self.sent_bytes = sent_bytes
        self.calls = 0

    def send(self, data: bytes) -> int:
        self.calls += 1
        if isinstance(self.sent_bytes, list):
            return self.sent_bytes.pop(0)
        return self.sent_bytes


class _FakeRecvSocket:
    def __init__(self, packets: list[bytes]):
        self._packets = list(packets)
        self._timeout = None
        self.closed = False

    def settimeout(self, timeout: float):
        self._timeout = timeout

    def recv(self, _bufsize: int) -> bytes:
        if self._packets:
            return self._packets.pop(0)
        return b""

    def close(self):
        self.closed = True


class PacketLoggingTests(unittest.TestCase):
    def setUp(self):
        session = get_session()
        session.reset()
        with session._lock:
            session._packet_log.clear()
            session.sock = None

        packet_log_service._current_log_path = None

    def test_init_packet_log_session_creates_file_and_prunes_to_ten(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            for idx in range(11):
                path = Path(tmpdir) / f"old-{idx}.jsonl"
                path.write_text("{}\n", encoding="utf-8")
                stamp = time.time() - (100 - idx)
                os_times = (stamp, stamp)
                Path(path).touch()
                os.utime(path, os_times)

            with patch.object(packet_log_service, "PACKET_LOG_DIR", tmpdir):
                created = packet_log_service.init_packet_log_session()

            files = sorted(Path(tmpdir).glob("*.jsonl"))
            self.assertTrue(Path(created).exists())
            self.assertEqual(len(files), packet_log_service.MAX_PACKET_LOG_FILES)

    def test_record_packet_appends_jsonl_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(packet_log_service, "PACKET_LOG_DIR", tmpdir):
            packet_log_service._current_log_path = None

            record_packet("aabbccdd", "UP")

            log_path = Path(packet_log_service.init_packet_log_session())
            lines = log_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            entry = json.loads(lines[0])
            self.assertEqual(entry["direction"], "UP")
            self.assertEqual(entry["raw_hex"], "aabbccdd")

    def test_send_packet_retries_until_full_packet_is_sent(self):
        session = get_session()
        session.sock = _FakeSendSocket(sent_bytes=[2, 1])

        with patch("core.connector.record_packet") as mock_record:
            sent = send_packet("aabbcc", use_queue=False)

        self.assertEqual(sent, 3)
        mock_record.assert_called_once_with(bytes.fromhex("aabbcc"), "UP")

    def test_send_and_receive_once_logs_inbound_response(self):
        session = get_session()
        sock = _FakeRecvSocket([bytes.fromhex("ccdd")])
        sock.send = lambda data: len(data)
        sock.gettimeout = lambda: None
        session.sock = sock

        with patch("core.connector.record_packet") as mock_record:
            response = send_and_receive_once("aabb", recv_timeout=1)

        self.assertEqual(response, bytes.fromhex("ccdd"))
        self.assertEqual(mock_record.call_args_list[0].args, (bytes.fromhex("aabb"), "UP"))
        self.assertEqual(mock_record.call_args_list[1].args, (bytes.fromhex("ccdd"), "DN"))

    def test_receive_loop_logs_raw_inbound_bytes(self):
        received = []
        stop_event = threading.Event()
        sock = _FakeRecvSocket([bytes.fromhex("aabb")])

        with patch("core.connector.record_packet") as mock_record:
            thread = start_receive_loop(sock, on_packet=received.append, stop_event=stop_event)
            thread.join(2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(received, [bytes.fromhex("aabb")])
        mock_record.assert_called_once_with(bytes.fromhex("aabb"), "DN")


if __name__ == "__main__":
    unittest.main()
