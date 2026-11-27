import json
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone

from bot import sim
from bot.config import Settings
from bot.demo import DemoProvider
from bot.engine import Engine
from bot.market import Article, Clock, Quote
from bot.scorer import ClaudeScorer, LexiconScorer
from bot.store import Store
from bot.timeutil import et_hhmm, parse_ts, to_et


class SimTests(unittest.TestCase):
    def test_fills_are_one_cent_worse(self):
        self.assertEqual(sim.entry_fill("LONG", 100.0, 0.01), 100.01)
        self.assertEqual(sim.exit_fill("LONG", 100.0, 0.01), 99.99)
        self.assertEqual(sim.entry_fill("SHORT", 100.0, 0.01), 99.99)
        self.assertEqual(sim.exit_fill("SHORT", 100.0, 0.01), 100.01)

    def test_round_trip_at_same_price_loses_two_cents(self):
        usd, _ = sim.pnl("LONG", sim.entry_fill("LONG", 50, 0.01), sim.exit_fill("LONG", 50, 0.01), 100)
        self.assertAlmostEqual(usd, -2.0)
        usd, _ = sim.pnl("SHORT", sim.entry_fill("SHORT", 50, 0.01), sim.exit_fill("SHORT", 50, 0.01), 100)
        self.assertAlmostEqual(usd, -2.0)

    def test_short_profits_when_price_falls(self):
        usd, pct = sim.pnl("SHORT", 99.99, 95.01, 10)
        self.assertGreater(usd, 0)
        self.assertGreater(pct, 0)


class TimeTests(unittest.TestCase):
    def test_eastern_dst(self):
        self.assertEqual(et_hhmm(datetime(2026, 11, 27, 14, 30, tzinfo=timezone.utc)), "09:30")  # EST
        self.assertEqual(et_hhmm(datetime(2026, 7, 1, 13, 30, tzinfo=timezone.utc)), "09:30")   # EDT

    def test_parse_nanoseconds(self):
        self.assertEqual(parse_ts("2026-11-27T14:30:00.123456789Z").microsecond, 123456)
        self.assertEqual(to_et(parse_ts("2026-11-27T09:30:00-05:00")).hour, 9)


class LexiconTests(unittest.TestCase):
    def test_direction(self):
        q = {"X": Quote("X", 20.0, prev_close=20.0)}
        now = datetime.now(timezone.utc)
        up = LexiconScorer().score([Article("1", "X beats estimates and raises guidance", "", ["X"], now)], q)
        dn = LexiconScorer().score([Article("2", "X misses estimates, cuts outlook", "", ["X"], now)], q)
        self.assertGreater(up[0].p_up, 70)
        self.assertLess(dn[0].p_up, 30)


class _FakeMessages:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        block = types.SimpleNamespace(type="text", text=json.dumps(self.payload))
        return types.SimpleNamespace(content=[block], stop_reason="end_turn",
                                     usage=types.SimpleNamespace(input_tokens=100, output_tokens=20))


class ClaudeScorerTests(unittest.TestCase):
    def test_parses_structured_output(self):
        try:
            sc = ClaudeScorer("claude-opus-5-5")
        except Exception:
            self.skipTest("anthropic SDK not installed")
        fake = _FakeMessages({"items": [{"article_id": "a1", "symbol": "X", "p_up": 83, "impact": "high", "reason": "beat"}]})
        sc.client = types.SimpleNamespace(beta=types.SimpleNamespace(messages=fake), messages=fake)
        q = {"X": Quote("X", 20.0, prev_close=19.0)}
        sigs = sc.score([Article("a1", "X beats", "", ["X"], datetime.now(timezone.utc))], q)
        self.assertEqual(len(sigs), 1)
        self.assertEqual(sigs[0].p_up, 83)
        self.assertEqual(fake.calls[0]["fallbacks"], "default")
        self.assertEqual(fake.calls[0]["output_config"]["format"]["type"], "json_schema")


class ExitRuleTests(unittest.TestCase):
    def _engine(self):
        s = Settings()
        e = Engine(s, None, LexiconScorer(), Store(tempfile.mkdtemp()))
        now = datetime(2026, 11, 27, 15, 0, tzinfo=timezone.utc)
        e.day = e._new_day("2026-11-27", now)
        e.day["session"] = {"open": "2026-11-27T14:30:00Z", "close": "2026-11-27T21:00:00Z", "half_day": False}
        clk = Clock(now, True, now + timedelta(days=1), parse_ts("2026-11-27T21:00:00Z"))
        return e, now, clk

    def _pos(self, side="LONG", entry=100.01, peak=0.0):
        return {"id": "t", "symbol": "X", "side": side, "shares": 100, "entry_time": "2026-11-27T14:40:00Z",
                "entry_market_price": 100.0, "entry_price": entry, "peak_pnl_pct": peak}

    def test_stop_loss(self):
        e, now, clk = self._engine()
        e.day["positions"] = [self._pos()]
        e.quotes = {"X": Quote("X", 98.3)}
        e._manage_exits(now, clk, {})
        self.assertEqual(len(e.day["trades"]), 1)
        self.assertIn("Stop-loss", e.day["trades"][0]["exit_reason"])

    def test_breakeven_protection_never_lets_winner_become_loser(self):
        e, now, clk = self._engine()
        e.day["positions"] = [self._pos(peak=1.0)]
        e.quotes = {"X": Quote("X", 100.08)}
        e._manage_exits(now, clk, {})
        t = e.day["trades"][0]
        self.assertIn("Break-even", t["exit_reason"])
        self.assertGreaterEqual(t["pnl_usd"], 0)

    def test_holds_when_nothing_triggers(self):
        e, now, clk = self._engine()
        e.day["positions"] = [self._pos()]
        e.quotes = {"X": Quote("X", 100.3)}
        e._manage_exits(now, clk, {})
        self.assertEqual(len(e.day["positions"]), 1)

    def test_end_of_day_flat(self):
        e, now, clk = self._engine()
        late = parse_ts("2026-11-27T20:52:00Z")
        e.day["positions"] = [dict(self._pos(), entry_time="2026-11-27T20:20:00Z")]
        e.quotes = {"X": Quote("X", 100.3)}
        e._manage_exits(late, clk, {})
        self.assertIn("End of day", e.day["trades"][0]["exit_reason"])


class DemoDayTest(unittest.TestCase):
    def test_full_demo_day_runs_and_grades(self):
        d = tempfile.mkdtemp()
        s = Settings()
        e = Engine(s, DemoProvider("2026-11-24", seed=3), LexiconScorer(), Store(d), tag="-demo")
        e.run(max_runtime_min=10_000)
        day = json.loads(open(f"{d}/days/2026-11-24-demo.json", encoding="utf-8").read())
        self.assertTrue(day["finalized"])
        self.assertEqual(day["positions"], [])
        for t in day["trades"]:
            self.assertIn("exit_reason", t)
            self.assertLessEqual(parse_ts(t["exit_time"]), parse_ts(day["session"]["close"]))
            self.assertIn("call_correct_at_close", t)
        self.assertTrue(all(p["correct"] is not None for p in day["predictions"]))


if __name__ == "__main__":
    unittest.main()
