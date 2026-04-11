import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from core.session import get_session
from features import battle
from services import flow_manager


class LoopBattleReconnectTests(unittest.TestCase):
    def setUp(self):
        session = get_session()
        session.reset()
        with session._lock:
            session.auto_reconnect_enabled = False
            session.reconnect_state = "idle"
            session.reconnect_attempts = 0
            session.reconnect_last_error = ""
            session.reconnect_next_retry_ts = 0.0
            session.reconnect_banned_until_ts = 0.0

    def test_start_loop_battle_round_preserves_loop_mode(self):
        session = get_session()
        with session._lock:
            session.battle_mode = "loop"
            session.battle_loop_running = True
            session.battle_loop_monster_code = "0001"
            session.battle_current_monster = "0001"

        with patch("features.battle._start_battle_round", return_value={"ok": True}) as start_round:
            res = battle.start_loop_battle_round("0001")

        self.assertTrue(res["ok"])
        self.assertTrue(session.battle_loop_running)
        self.assertEqual(session.battle_mode, "loop")
        start_round.assert_called_once_with("0001", run_pre_battle_actions=False)

    def test_battle_wait_timeout_has_grace_window(self):
        session = get_session()
        now = time.time()
        with session._lock:
            session.battle_state = battle.BATTLE_STATE_WAITING_START_RESPONSE
            session.battle_wait_deadline_ts = now - 0.2
            session.battle_last_response_ts = 0.0
        self.assertFalse(battle.is_battle_wait_timed_out(now))

        with session._lock:
            session.battle_wait_deadline_ts = now - 1.0
            session.battle_last_response_ts = now - 0.2
        self.assertFalse(battle.is_battle_wait_timed_out(now))

        with session._lock:
            session.battle_last_response_ts = 0.0
        self.assertTrue(battle.is_battle_wait_timed_out(now))

    def test_disconnect_handler_keeps_loop_and_schedules_reconnect(self):
        session = get_session()
        with session._lock:
            session.connected = True
            session.connection_status = "connected"
            session.battle_mode = "loop"
            session.battle_loop_running = True
            session.battle_loop_monster_code = "0001"

        with patch("services.flow_manager.stop_connection_runtime"), patch(
            "services.flow_manager._schedule_reconnect", return_value=True
        ) as schedule_reconnect:
            flow_manager._default_disconnect_handler(Exception("boom"))

        self.assertFalse(session.connected)
        self.assertTrue(session.battle_loop_running)
        self.assertEqual(session.battle_mode, "loop")
        schedule_reconnect.assert_called_once()

    def test_control_worker_next_round_uses_loop_round_starter(self):
        session = get_session()
        now = time.time()
        with session._lock:
            session.connected = True
            session.reconnect_state = "idle"
            session.battle_state = battle.BATTLE_STATE_ENDED
            session.battle_mode = "loop"
            session.battle_loop_running = True
            session.battle_loop_monster_code = "0001"
            session.battle_next_start_ts = now - 0.1

        with patch("services.flow_manager.start_loop_battle_round", return_value={"ok": True}) as start_round:
            flow_manager._control_worker_tick(now)

        start_round.assert_called_once_with("0001", run_pre_battle_actions=True)
        self.assertTrue(session.battle_loop_running)

    def test_control_worker_resumes_loop_after_reconnect_from_idle_state(self):
        session = get_session()
        now = time.time()
        with session._lock:
            session.connected = True
            session.reconnect_state = "idle"
            session.battle_state = battle.BATTLE_STATE_IDLE
            session.battle_mode = "loop"
            session.battle_loop_running = True
            session.battle_loop_monster_code = "0001"
            session.battle_current_monster = "0001"
            session.battle_next_start_ts = now - 0.1

        with patch("services.flow_manager.start_loop_battle_round", return_value={"ok": True}) as start_round:
            flow_manager._control_worker_tick(now)

        start_round.assert_called_once_with("0001", run_pre_battle_actions=True)
        self.assertTrue(session.battle_loop_running)

    def test_reconnect_fail_after_three_attempts_keeps_loop_intent(self):
        session = get_session()
        with session._lock:
            session.account = "acc"
            session.login_password = "pwd"
            session.login_server_name = "srv"
            session.server_ip = "127.0.0.1"
            session.server_port = 1234
            session.server_name = "srv"
            session.reconnect_role_id = "role"
            session.reconnect_attempts = 2
            session.reconnect_max_attempts = 3
            session.battle_mode = "loop"
            session.battle_loop_running = True

        with patch("services.flow_manager.login_flow", return_value={"ok": False, "error": "login failed"}):
            flow_manager._perform_backend_reconnect()

        self.assertEqual(session.reconnect_state, "failed")
        self.assertEqual(session.reconnect_attempts, 3)
        self.assertTrue(session.battle_loop_running)
        self.assertEqual(session.battle_mode, "loop")

    def test_retry_delay_is_aggressive_but_capped(self):
        self.assertEqual(flow_manager._get_retry_delay_s(1), 0.0)
        self.assertEqual(flow_manager._get_retry_delay_s(2), 1.0)
        self.assertEqual(flow_manager._get_retry_delay_s(3), 2.0)
        self.assertEqual(flow_manager._get_retry_delay_s(9), 2.0)

    def test_stop_battle_loop_cancels_pending_reconnect(self):
        session = get_session()
        with session._lock:
            session.battle_mode = "loop"
            session.battle_loop_running = True
            session.reconnect_state = "scheduled"
            session.reconnect_attempts = 2
            session.reconnect_next_retry_ts = time.time() + 10

        res = battle.stop_battle_loop("stop")

        self.assertTrue(res["ok"])
        self.assertFalse(session.battle_loop_running)
        self.assertEqual(session.reconnect_state, "idle")
        self.assertEqual(session.reconnect_attempts, 0)
        self.assertEqual(session.reconnect_next_retry_ts, 0.0)

    def test_control_worker_cancels_scheduled_reconnect_when_loop_stopped(self):
        session = get_session()
        with session._lock:
            session.connected = False
            session.auto_reconnect_enabled = False
            session.battle_loop_running = False
            session.reconnect_state = "scheduled"
            session.reconnect_next_retry_ts = time.time() + 10

        flow_manager._control_worker_tick(time.time())

        self.assertEqual(session.reconnect_state, "idle")
        self.assertEqual(session.reconnect_next_retry_ts, 0.0)

    def test_battle_wait_timeout_does_not_disconnect(self):
        session = get_session()
        now = time.time()
        with session._lock:
            session.connected = True
            session.reconnect_state = "idle"
            session.battle_state = battle.BATTLE_STATE_WAITING_ACTION_RESULT
            session.battle_wait_deadline_ts = now - 1.0
            session.battle_last_response_ts = 0.0
            session.battle_loop_running = True

        with patch.object(flow_manager, "_default_disconnect_handler") as disc, patch.object(
            flow_manager, "recover_battle_wait_timeout_with_f703", return_value={"ok": True, "recover_count": 1}
        ):
            flow_manager._control_worker_tick(now)

        disc.assert_not_called()

    def test_recover_resets_counter_on_battle_de07_mark(self):
        session = get_session()
        with session._lock:
            session.battle_f703_timeout_recover_count = 2
        hex_body = "aa" * 10 + "030100de07" + "bb"
        battle.handle_battle_server_packet(hex_body)
        with session._lock:
            self.assertEqual(session.battle_f703_timeout_recover_count, 0)


if __name__ == "__main__":
    unittest.main()
