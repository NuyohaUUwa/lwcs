import time
import unittest
from unittest.mock import patch

from game_test.backend.domain.combat import battle
from game_test.backend.runtime.session import GameSession
import game_test.backend.runtime.session as session_module
from game_test.backend.runtime import flow


def _packet(fingerprint: str, text: str = "", tail: str = "") -> str:
    return "00000000" + fingerprint + tail + text.encode("utf-8").hex()


DE07 = "e8030100de07"
DF07 = "e8030100df07"
E207 = "e8030100e207"


class BattleStateMachineTest(unittest.TestCase):
    def setUp(self):
        session_module._session = GameSession()
        self.session = session_module.get_session()
        self.session.connected = True
        self.session.sock = object()
        self.session.reconnect_state = "idle"

    def tearDown(self):
        session_module._session = None

    def test_normal_loop_reaches_cooldown_after_e207_victory(self):
        with patch("game_test.backend.runtime.actions.send_raw_action", return_value={"ok": True}):
            start = battle.start_battle_loop("abcd", loop_delay_ms=10)
            self.assertTrue(start["ok"])
            self.assertEqual(self.session.battle_state, battle.BATTLE_STATE_WAITING_DE07)

            de07 = battle.handle_battle_server_packet(_packet(DE07, "怪物角色"))
            self.assertTrue(de07["ok"])
            self.assertEqual(self.session.battle_state, battle.BATTLE_STATE_WAITING_DF07)
            self.assertEqual(self.session.battle_last_action, "f703")

            pending = battle.handle_battle_server_packet(_packet(DF07, tail="00000000e2070000"))
            self.assertTrue(pending["ok"])
            self.assertEqual(self.session.battle_state, battle.BATTLE_STATE_WAITING_DF07)
            self.assertEqual(self.session.battle_last_action, "f703")

            settlement = battle.handle_battle_server_packet(_packet(E207, "获得经验：12/获得金币: 3铜"))
            self.assertTrue(settlement["ok"])
            self.assertEqual(self.session.battle_state, battle.BATTLE_STATE_COOLDOWN)
            self.assertTrue(self.session.battle_loop_running)
            self.assertEqual(self.session.battle_total_count, 1)
            self.assertEqual(self.session.battle_total_exp, 12)
            self.assertEqual(self.session.battle_total_gold_copper, 3)
            self.assertGreater(self.session.battle_next_start_ts, 0)

    def test_de07_protect_time_resends_f603_and_errors_after_retry_limit(self):
        protect_packet = _packet(DE07, "登录保护时间，不可打怪")
        with patch("game_test.backend.runtime.actions.send_raw_action", return_value={"ok": True}) as send_raw:
            with patch("game_test.backend.domain.combat.battle.time.sleep", return_value=None):
                battle.start_battle_loop("abcd", loop_delay_ms=0)
                for _ in range(3):
                    res = battle.handle_battle_server_packet(protect_packet)
                    self.assertTrue(res["ok"])
                    self.assertEqual(self.session.battle_state, battle.BATTLE_STATE_WAITING_DE07)
                res = battle.handle_battle_server_packet(protect_packet)
                self.assertTrue(res["ok"])
                self.assertEqual(self.session.battle_state, battle.BATTLE_STATE_ERROR)
                self.assertEqual(send_raw.call_count, 4)

    def test_df07_battle_already_ended_schedules_next_loop_without_stopping(self):
        with patch("game_test.backend.runtime.actions.send_raw_action", return_value={"ok": True}):
            battle.start_battle_loop("abcd", loop_delay_ms=0)
            battle.handle_battle_server_packet(_packet(DE07, "怪物角色"))
            res = battle.handle_battle_server_packet(_packet(DF07, "战斗已结束"))
            self.assertTrue(res["ok"])
            self.assertTrue(self.session.battle_loop_running)
            self.assertEqual(self.session.battle_state, battle.BATTLE_STATE_COOLDOWN)
            self.assertGreaterEqual(self.session.battle_next_start_ts, 0)

            self.session.battle_next_start_ts = time.time() - 1
            with patch("game_test.backend.runtime.flow.start_loop_battle_round", return_value={"ok": True}) as restart:
                flow._control_worker_tick(time.time())
                restart.assert_called_once()

    def test_df07_no_energy_stops_loop_and_returns_idle(self):
        with patch("game_test.backend.runtime.actions.send_raw_action", return_value={"ok": True}):
            battle.start_battle_loop("abcd", loop_delay_ms=0)
            battle.handle_battle_server_packet(_packet(DE07, "怪物角色"))
            res = battle.handle_battle_server_packet(_packet(DF07, "内力不足"))
            self.assertTrue(res["ok"])
            self.assertFalse(self.session.battle_loop_running)
            self.assertEqual(self.session.battle_state, battle.BATTLE_STATE_IDLE)
            self.assertEqual(self.session.battle_next_start_ts, 0.0)

    def test_e207_defeat_stops_loop(self):
        with patch("game_test.backend.runtime.actions.send_raw_action", return_value={"ok": True}):
            battle.start_battle_loop("abcd", loop_delay_ms=0)
            battle.handle_battle_server_packet(_packet(DE07, "怪物角色"))
            res = battle.handle_battle_server_packet(_packet(E207, "失去气血"))
            self.assertTrue(res["ok"])
            self.assertFalse(self.session.battle_loop_running)
            self.assertEqual(self.session.battle_state, battle.BATTLE_STATE_IDLE)

    def test_timeout_recovery_uses_state_specific_packets(self):
        with patch("game_test.backend.runtime.actions.send_raw_action", return_value={"ok": True}):
            battle.mark_battle_started("abcd")
            f603 = battle.recover_battle_wait_timeout_resend_f603()
            self.assertTrue(f603["ok"])
            self.assertEqual(self.session.battle_state, battle.BATTLE_STATE_WAITING_DE07)

            battle._set_battle_state(
                state=battle.BATTLE_STATE_WAITING_DF07,
                in_progress=True,
                current_monster="abcd",
                last_action="f703",
            )
            f703 = battle.recover_battle_wait_timeout_with_f703()
            self.assertTrue(f703["ok"])
            self.assertEqual(self.session.battle_state, battle.BATTLE_STATE_WAITING_DF07)


if __name__ == "__main__":
    unittest.main()
