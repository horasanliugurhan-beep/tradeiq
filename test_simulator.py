import copy
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from simulator import D, DEFAULTS, Engine, load_events, replay


def event(i, price="100", action="hold", reject="", symbol="SOL/USDT", day="01"):
    return dict(id=str(i), timestamp=f"2026-10-{day}T10:{i:02}:00+03:00",
                symbol=symbol, price=str(price), action=action, reject=reject)


class SimulatorTests(unittest.TestCase):
    def test_buy_debits_cash_but_not_whole_allocation_from_equity(self):
        e = Engine()
        e.process(event(1, action="buy"))
        p = e.state["positions"]["SOL/USDT"]
        self.assertLess(D(e.state["cash"]), D(1000))
        self.assertEqual(e.equity(), D(e.state["cash"]) + D(p["qty"]) * 100)
        self.assertFalse(e.state["killed"])
        self.assertGreater(e.equity(), D(999))

    def test_buy_rejection_never_creates_position_or_fee(self):
        e = Engine()
        e.process(event(1, action="buy", reject="buy"))
        self.assertEqual(e.state["cash"], "1000")
        self.assertFalse(e.state["positions"])
        self.assertFalse(e.state["fills"])

    def test_sell_rejection_keeps_position_then_retry_closes(self):
        e = Engine()
        e.process(event(1, action="buy"))
        cash = e.state["cash"]
        e.process(event(2, price=101, action="sell", reject="sell"))
        self.assertIn("SOL/USDT", e.state["positions"])
        self.assertEqual(e.state["cash"], cash)
        e.process(event(3, price=101, action="sell"))
        self.assertFalse(e.state["positions"])
        self.assertEqual(len(e.state["fills"]), 2)

    def test_exact_cash_fee_pnl_reconciliation(self):
        c = dict(DEFAULTS, slippage="0", fee="0.001", allocation="100", risk="0.5")
        e = Engine(c)
        e.process(event(1, action="buy"))
        e.process(event(2, price=103, action="sell"))
        self.assertEqual(D(e.state["cash"]), D("1002.797"))
        self.assertEqual(D(e.state["fees"]), D("0.203"))
        self.assertEqual(D(e.state["realized_pnl"]), D("2.797"))
        self.assertEqual(D(e.summary()["net_pnl"]), D("2.797"))

    def test_kill_switch_blocks_buy_but_stop_still_sells(self):
        e = Engine()
        e.process(event(1, action="buy"))
        e.process(event(2, price=80, reject="sell"))
        self.assertTrue(e.state["killed"])
        self.assertIn("SOL/USDT", e.state["positions"])
        e.process(event(3, action="buy", symbol="BTC/USDT"))
        self.assertEqual(e.state["events"][-1]["outcome"], "buy_blocked_daily_limit")
        e.process(event(4, price=80))
        self.assertFalse(e.state["positions"])
        self.assertEqual(e.state["fills"][-1]["reason"], "stop")

    def test_killed_position_can_exit_by_signal(self):
        e = Engine(dict(DEFAULTS, daily_limit="0.0001"))
        e.process(event(1, action="buy"))
        self.assertTrue(e.state["killed"])
        e.process(event(2, action="sell"))
        self.assertFalse(e.state["positions"])
        self.assertEqual(e.state["fills"][-1]["reason"], "signal")

    def test_take_profit(self):
        e = Engine()
        e.process(event(1, action="buy"))
        e.process(event(2, price=112))
        self.assertEqual(e.state["fills"][-1]["reason"], "take_profit")
        self.assertFalse(e.state["positions"])

    def test_day_rollover_reenables_buy(self):
        e = Engine()
        e.process(event(1, action="buy"))
        e.process(event(2, price=80))
        self.assertTrue(e.state["killed"])
        e.process(event(3, action="buy", day="02"))
        self.assertFalse(e.state["killed"])
        self.assertIn("SOL/USDT", e.state["positions"])

    def test_all_open_positions_are_in_equity(self):
        e = Engine()
        e.process(event(1, action="buy"))
        e.process(event(2, action="buy", symbol="BTC/USDT", price=200))
        e.process(event(3, price=101))
        expected = D(e.state["cash"]) + sum(D(p["qty"]) * D(e.state["marks"][sym])
                                             for sym, p in e.state["positions"].items())
        self.assertEqual(e.equity(), expected)

    def test_position_limit(self):
        e = Engine(dict(DEFAULTS, max_positions=1))
        e.process(event(1, action="buy"))
        e.process(event(2, action="buy", symbol="BTC/USDT"))
        self.assertEqual(e.state["events"][-1]["outcome"], "buy_blocked_position_limit")

    def test_low_cash_rounding_and_minimum(self):
        e = Engine(dict(DEFAULTS, initial_cash="6", risk="0.9", allocation="100"))
        e.process(event(1, action="buy"))
        self.assertGreaterEqual(D(e.state["cash"]), 0)
        self.assertTrue(e.state["positions"])
        e.process(event(2, action="buy", symbol="BTC/USDT"))
        self.assertEqual(e.state["events"][-1]["outcome"], "buy_rejected_min_notional")

    def test_restart_matches_uninterrupted_replay_including_rejections(self):
        events, digest = load_events(Path(__file__).parent / "data/demo.csv")
        full = Engine(dataset=digest)
        replay(events, full)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            partial = Engine(state_path=path, dataset=digest)
            replay(events, partial, 4)
            resumed = Engine(state_path=path, dataset=digest)
            self.assertIn("SOL/USDT", resumed.state["positions"])
            replay(events, resumed)
            self.assertEqual(full.state, resumed.state)
            replay(events, resumed)
            self.assertEqual(full.state, resumed.state)

    def test_persist_kill_switch_and_stop_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            e = Engine(state_path=path)
            e.process(event(1, action="buy"))
            e.process(event(2, price=80, reject="sell"))
            e = Engine(state_path=path)
            self.assertTrue(e.state["killed"])
            e.process(event(3, price=80))
            self.assertEqual(e.state["fills"][-1]["reason"], "stop")

    def test_bad_checkpoint_is_not_silently_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{bad", encoding="utf-8")
            with self.assertRaises(ValueError):
                Engine(state_path=path)

    def test_changed_dataset_or_config_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            Engine(state_path=path, dataset="a").save()
            with self.assertRaises(ValueError):
                Engine(state_path=path, dataset="b")
            with self.assertRaises(ValueError):
                Engine(dict(DEFAULTS, fee="0.002"), state_path=path, dataset="a")

    def test_tampered_cash_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            e = Engine(state_path=path)
            e.process(event(1, action="buy"))
            data = json.loads(path.read_text(encoding="utf-8"))
            data["cash"] = "1000"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                Engine(state_path=path)

    def test_checkpoint_write_failure_rolls_back_entire_event(self):
        e = Engine()
        before = copy.deepcopy(e.state)
        with patch.object(e, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                e.process(event(1, action="buy"))
        self.assertEqual(e.state, before)

    def test_invalid_numbers_and_configuration(self):
        for price in ("NaN", "Infinity", "0", "-1"):
            with self.subTest(price=price), self.assertRaises(ValueError):
                Engine().process(event(1, price=price))
        for key, value in (("fee", "1"), ("max_positions", 0), ("initial_cash", "0")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                Engine(dict(DEFAULTS, **{key: value}))

    def test_duplicate_event_is_not_applied_again(self):
        e = Engine()
        e.process(event(1, action="buy"))
        before = copy.deepcopy(e.state)
        with self.assertRaises(ValueError):
            e.process(event(1, action="buy"))
        self.assertEqual(e.state, before)

    def test_input_validation(self):
        header = "id,timestamp,symbol,price,action,reject\n"
        bad = ["1,2026-10-01T10:00:00,SOL/USDT,100,buy,\n",
               "1,2026-10-01T10:00:00+03:00,SOL/USDT,NaN,buy,\n",
               "1,2026-10-01T10:00:00+03:00,SOL/USDT,100,short,\n",
               "1,2026-10-01T10:00:00+03:00,SOL/USDT,100,buy,\n" * 2,
               "2,2026-10-02T10:00:00+03:00,SOL/USDT,100,buy,\n"
               "1,2026-10-01T10:00:00+03:00,SOL/USDT,100,buy,\n"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.csv"
            for body in bad:
                path.write_text(header + body, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_events(path)

    def test_full_demo_runs_with_network_disabled(self):
        events, digest = load_events(Path(__file__).parent / "data/demo.csv")
        with patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden")), \
             patch.object(socket, "create_connection", side_effect=AssertionError("Network forbidden")):
            summary = replay(events, Engine(dataset=digest))
        self.assertEqual(summary["events"], 12)
        self.assertFalse(summary["open_positions"])
        self.assertEqual(summary["fills"], 6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
