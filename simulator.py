"""Offline, long-only signal replay. Standard library only; no live adapter."""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import os
import re
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

D = Decimal
DEFAULTS = dict(initial_cash="1000", fee="0.001", slippage="0.0005",
                allocation="250", risk="0.01", stop_pct="0.05",
                reward_ratio="2", daily_limit="0.02", min_notional="5",
                quantity_step="0.000001", max_positions=3)


def number(value, positive=False):
    text = str(value)
    if len(text) > 128:
        raise ValueError("Numeric input is too long")
    try:
        result = D(text)
    except ArithmeticError:
        raise ValueError("Invalid numeric input") from None
    if not result.is_finite() or (result <= 0 if positive else result < 0):
        raise ValueError("Finite nonnegative number required (positive for price/size)")
    if not -60 <= result.as_tuple().exponent <= 18 or result.adjusted() > 18:
        raise ValueError("Numeric input is outside supported bounds")
    return result


def validate_event(row):
    if not isinstance(row, dict) or set(row) != {"id", "timestamp", "symbol", "price", "action", "reject"}:
        raise ValueError("Invalid event fields")
    if not isinstance(row["id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", row["id"]):
        raise ValueError("Invalid event id")
    if not isinstance(row["symbol"], str) or not re.fullmatch(r"[A-Z0-9]{1,15}/USDT", row["symbol"]):
        raise ValueError("Invalid symbol")
    if row["action"] not in ("buy", "sell", "hold") or row["reject"] not in ("", "buy", "sell", "both"):
        raise ValueError("Invalid action/rejection")
    moment = datetime.fromisoformat(row["timestamp"])
    if moment.utcoffset() is None:
        raise ValueError("Timestamp must have timezone")
    number(row["price"], True)
    return moment


def encode(value):
    if isinstance(value, D):
        return str(value)
    raise TypeError(type(value).__name__)


def config_checked(config):
    if set(config) != set(DEFAULTS):
        raise ValueError("Unknown or missing configuration fields")
    config = dict(config)
    for key in config:
        if key != "max_positions":
            config[key] = str(number(config[key], positive=key not in ("fee", "slippage")))
    for key in ("fee", "slippage", "risk", "stop_pct", "daily_limit"):
        if D(config[key]) >= 1:
            raise ValueError(f"{key} must be below 1")
    if type(config["max_positions"]) is not int or config["max_positions"] < 1:
        raise ValueError("max_positions must be a positive integer")
    return config


class FakeBroker:
    """Deterministic fills/rejections. Never owns cash or connects anywhere."""
    def fill(self, side, price, quantity, event, config):
        if event["reject"] in (side, "both"):
            return None
        slip = D(config["slippage"])
        fill_price = price * (1 + slip if side == "buy" else 1 - slip)
        notional = quantity * fill_price
        return dict(price=fill_price, qty=quantity, notional=notional,
                    fee=notional * D(config["fee"]))


class MemoryNotifier:
    """Notification substitute: returned data only, no Telegram dependency."""
    def message(self, outcome, symbol):
        return f"SIMULATION {symbol}: {outcome}"


def load_events(path):
    raw = Path(path).read_bytes()
    rows = list(csv.DictReader(raw.decode("utf-8-sig").splitlines()))
    if not rows:
        raise ValueError("Empty event file")
    previous = None
    offset = None
    ids = set()
    events = []
    for row in rows:
        validate_event(row)
        if set(row) != {"id", "timestamp", "symbol", "price", "action", "reject"}:
            raise ValueError("CSV requires id,timestamp,symbol,price,action,reject")
        moment = datetime.fromisoformat(row["timestamp"])
        if moment.utcoffset() is None or (previous is not None and moment < previous):
            raise ValueError("Chronological timestamps with timezone required")
        if offset is not None and moment.utcoffset() != offset:
            raise ValueError("Use one consistent timezone offset for calendar loss limits")
        offset = moment.utcoffset()
        if not row["id"] or row["id"] in ids:
            raise ValueError("Event ids must be unique and nonempty")
        if not row["symbol"].endswith("/USDT") or not row["symbol"][:-5].isalnum():
            raise ValueError("Use BASE/USDT symbols")
        if row["action"] not in ("buy", "sell", "hold"):
            raise ValueError("Action must be buy/sell/hold")
        if row["reject"] not in ("", "buy", "sell", "both"):
            raise ValueError("Reject must be empty/buy/sell/both")
        number(row["price"], positive=True)
        previous = moment
        ids.add(row["id"])
        events.append(row)
    return events, hashlib.sha256(raw).hexdigest()


class Engine:
    def __init__(self, config=None, state_path=None, dataset="", broker=None):
        self.config = config_checked(config or DEFAULTS)
        self.path = Path(state_path) if state_path else None
        self.broker = broker or FakeBroker()
        self.notifier = MemoryNotifier()
        initial = self.config["initial_cash"]
        self.state = dict(schema=1, config=self.config, dataset=dataset, cursor=0,
                          cash=initial, positions={}, marks={}, fills=[], events=[],
                          day="", daily_start=initial, killed=False, peak=initial,
                          max_drawdown="0", realized_pnl="0", fees="0")
        if self.path and self.path.exists():
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
            if (self.state.get("schema") != 1 or self.state.get("config") != self.config
                    or self.state.get("dataset") != dataset):
                raise ValueError("Checkpoint version/configuration/dataset mismatch")
            self.validate()

    def equity(self):
        return D(self.state["cash"]) + sum(
            (D(p["qty"]) * D(self.state["marks"][s])
             for s, p in self.state["positions"].items()), D(0))

    def guard(self):
        s = self.state
        equity = self.equity()
        start = D(s["daily_start"])
        if start > 0 and equity <= start * (1 - D(self.config["daily_limit"])):
            s["killed"] = True
        s["peak"] = str(max(D(s["peak"]), equity))
        peak = D(s["peak"])
        s["max_drawdown"] = str(max(D(s["max_drawdown"]), (peak - equity) / peak))

    def validate(self):
        s = self.state
        cash = number(s["cash"])
        if type(s["cursor"]) is not int or s["cursor"] != len(s["events"]):
            raise ValueError("Invalid checkpoint cursor")
        ids = [x["id"] for x in s["events"]]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate checkpoint event ids")
        expected = D(self.config["initial_cash"])
        quantities = {}
        costs = {}
        entries = {}
        realized = D(0)
        fees = D(0)
        for fill in s["fills"]:
            qty = number(fill["qty"], True)
            price = number(fill["price"], True)
            fee = number(fill["fee"])
            if fee != qty * price * D(self.config["fee"]):
                raise ValueError("Invalid fill fee")
            side, sym = fill["side"], fill["symbol"]
            if side not in ("buy", "sell"):
                raise ValueError("Invalid fill side")
            expected += -(qty * price + fee) if side == "buy" else qty * price - fee
            quantities[sym] = quantities.get(sym, D(0)) + (qty if side == "buy" else -qty)
            if quantities[sym] < 0:
                raise ValueError("Negative inventory")
            fees += fee
            if side == "buy":
                if sym in costs:
                    raise ValueError("Multiple entries in one position")
                costs[sym] = qty * price + fee
                entries[sym] = price
                if D(fill["realized_pnl"]) != 0:
                    raise ValueError("Buy cannot realize PnL")
            else:
                if sym not in costs or quantities[sym] != 0:
                    raise ValueError("Only complete position exits supported")
                pnl = qty * price - fee - costs.pop(sym)
                entries.pop(sym)
                if pnl != D(fill["realized_pnl"]):
                    raise ValueError("Invalid realized PnL")
                realized += pnl
        if expected != cash or fees != D(s["fees"]):
            raise ValueError("Cash/fee ledger does not reconcile")
        if realized != D(s["realized_pnl"]):
            raise ValueError("Realized PnL does not reconcile")
        actual = {sym: D(p["qty"]) for sym, p in s["positions"].items()}
        if {k: v for k, v in quantities.items() if v} != actual:
            raise ValueError("Position ledger does not reconcile")
        for sym, p in s["positions"].items():
            number(s["marks"][sym], True)
            for key in ("qty", "entry", "cost", "stop", "target"):
                number(p[key], True)
            if D(p["stop"]) >= D(p["entry"]) or D(p["target"]) <= D(p["entry"]):
                raise ValueError("Invalid stop/target")
            if D(p["cost"]) != costs[sym] or D(p["entry"]) != entries[sym]:
                raise ValueError("Position cost basis does not reconcile")
            distance = entries[sym] * D(self.config["stop_pct"])
            if (D(p["stop"]) != entries[sym] - distance or
                    D(p["target"]) != entries[sym] + distance * D(self.config["reward_ratio"])):
                raise ValueError("Checkpoint risk levels do not match configuration")
        number(s["peak"], True)
        number(s["daily_start"])
        if number(s["max_drawdown"]) > 1:
            raise ValueError("Invalid maximum drawdown")
        if type(s["killed"]) is not bool:
            raise ValueError("Invalid kill switch")

    def save(self):
        self.validate()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(self.path.suffix + ".tmp")
            with temp.open("w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2, ensure_ascii=False, default=encode)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, self.path)

    def process(self, event):
        """One event is one atomic ledger + checkpoint transaction."""
        before = copy.deepcopy(self.state)
        try:
            self._process(event)
            self.save()
        except Exception:
            self.state = before
            raise

    def _process(self, event):
        s, c = self.state, self.config
        moment = validate_event(event)
        if s["events"]:
            previous = datetime.fromisoformat(s["events"][-1]["timestamp"])
            if moment < previous or moment.utcoffset() != previous.utcoffset():
                raise ValueError("Events must be chronological with consistent timezone")
        if event["id"] in {x["id"] for x in s["events"]}:
            raise ValueError("Duplicate event")
        price = number(event["price"], True)
        sym = event["symbol"]
        # Calendar is the timestamp's explicit timezone (sample: Europe/Istanbul).
        day = datetime.fromisoformat(event["timestamp"]).date().isoformat()
        if day != s["day"]:
            s.update(day=day, daily_start=str(self.equity()), killed=False)
        s["marks"][sym] = str(price)
        self.guard()  # portfolio equity, including open inventory
        outcome = "hold"
        pos = s["positions"].get(sym)
        if pos:
            reason = ("stop" if price <= D(pos["stop"]) else
                      "take_profit" if price >= D(pos["target"]) else
                      "signal" if event["action"] == "sell" else None)
            if reason:
                fill = self.broker.fill("sell", price, D(pos["qty"]), event, c)
                if fill is None:
                    outcome = "sell_rejected"
                else:
                    proceeds = fill["notional"] - fill["fee"]
                    pnl = proceeds - D(pos["cost"])
                    s["cash"] = str(D(s["cash"]) + proceeds)
                    s["realized_pnl"] = str(D(s["realized_pnl"]) + pnl)
                    del s["positions"][sym]
                    self.record_fill(event, "sell", fill, reason, pnl)
                    outcome = reason
        elif event["action"] == "buy":
            if s["killed"]:
                outcome = "buy_blocked_daily_limit"
            elif len(s["positions"]) >= c["max_positions"]:
                outcome = "buy_blocked_position_limit"
            else:
                entry = price * (1 + D(c["slippage"]))
                distance = entry * D(c["stop_pct"])
                # Include entry and estimated stop exit fees/slippage in risk sizing.
                stop_fill = (entry - distance) * (1 - D(c["slippage"]))
                unit_risk = entry * (1 + D(c["fee"])) - stop_fill * (1 - D(c["fee"]))
                qty = min(self.equity() * D(c["risk"]) / unit_risk,
                          D(c["allocation"]) / entry,
                          D(s["cash"]) / (entry * (1 + D(c["fee"]))))
                step = D(c["quantity_step"])
                qty = (qty / step).to_integral_value(rounding=ROUND_DOWN) * step
                if qty <= 0 or qty * entry < D(c["min_notional"]):
                    outcome = "buy_rejected_min_notional"
                else:
                    fill = self.broker.fill("buy", price, qty, event, c)
                    if fill is None:
                        outcome = "buy_rejected"
                    else:
                        cost = fill["notional"] + fill["fee"]
                        if cost > D(s["cash"]):
                            raise ValueError("Provider fill exceeds available cash")
                        s["cash"] = str(D(s["cash"]) - cost)
                        s["positions"][sym] = dict(qty=str(fill["qty"]), entry=str(fill["price"]),
                            cost=str(cost), stop=str(fill["price"] - distance),
                            target=str(fill["price"] + distance * D(c["reward_ratio"])))
                        self.record_fill(event, "buy", fill, "signal", D(0))
                        outcome = "buy_filled"
        self.guard()  # exit/entry fees may themselves cross the loss threshold
        s["events"].append(dict(id=event["id"], timestamp=event["timestamp"], symbol=sym,
            outcome=outcome, cash=s["cash"], equity=str(self.equity()), killed=s["killed"],
            open_positions=len(s["positions"]), notification=self.notifier.message(outcome, sym)))
        s["cursor"] += 1

    def record_fill(self, event, side, fill, reason, pnl):
        self.state["fees"] = str(D(self.state["fees"]) + fill["fee"])
        self.state["fills"].append(dict(event_id=event["id"], symbol=event["symbol"],
            side=side, price=str(fill["price"]), qty=str(fill["qty"]),
            fee=str(fill["fee"]), reason=reason, realized_pnl=str(pnl)))

    def summary(self):
        s = self.state
        equity = self.equity()
        unrealized = sum((D(p["qty"]) * D(s["marks"][sym]) - D(p["cost"])
                          for sym, p in s["positions"].items()), D(0))
        return dict(mode="OFFLINE_SYNTHETIC", events=s["cursor"], fills=len(s["fills"]),
            initial_cash=self.config["initial_cash"], cash=s["cash"], equity=str(equity),
            net_pnl=str(equity - D(self.config["initial_cash"])), realized_pnl=s["realized_pnl"],
            unrealized_pnl=str(unrealized), fees=s["fees"], open_positions=s["positions"],
            max_drawdown_pct=str(D(s["max_drawdown"]) * 100), killed=s["killed"],
            valuation="cash + quantity * last observed price; future exit fees excluded")


def replay(events, engine, max_events=None):
    cursor = engine.state["cursor"]
    if cursor > len(events) or [x["id"] for x in engine.state["events"]] != [x["id"] for x in events[:cursor]]:
        raise ValueError("Checkpoint event prefix mismatch")
    pending = events[cursor:]
    if max_events is not None:
        if max_events < 0:
            raise ValueError("max-events must be nonnegative")
        pending = pending[:max_events]
    for event in pending:
        engine.process(event)
    return engine.summary()


def main():
    parser = argparse.ArgumentParser(description="Offline synthetic signal replay")
    parser.add_argument("--data", type=Path, default=Path(__file__).parent / "data" / "demo.csv")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "results")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--max-events", type=int)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8")) if args.config else DEFAULTS
    events, digest = load_events(args.data)
    engine = Engine(config, args.output / "checkpoint.json", digest)
    summary = replay(events, engine, args.max_events)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    for name in ("events", "fills"):
        rows = engine.state[name]
        if rows:
            with (args.output / (name + ".csv")).open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
