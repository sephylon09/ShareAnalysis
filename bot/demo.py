"""A fake market for testing the whole pipeline offline (no API keys, runs a full day in seconds).

Prices follow a random walk; each synthetic headline pushes its stock in the headline's direction
~70% of the time (and the opposite way otherwise), so the strategy can both win and lose.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .market import Article, Clock, HotSymbol, Quote
from .timeutil import et_offset

STOCKS = {
    "NVDA": ("Nvidia", 182.0), "AAPL": ("Apple", 271.0), "TSLA": ("Tesla", 402.0), "AMD": ("AMD", 214.0),
    "PLTR": ("Palantir", 168.0), "SMCI": ("Super Micro", 41.0), "COIN": ("Coinbase", 268.0), "MRNA": ("Moderna", 27.0),
    "BA": ("Boeing", 196.0), "NFLX": ("Netflix", 108.0), "HOOD": ("Robinhood", 121.0), "UBER": ("Uber", 88.0),
}
POS = [
    ("{n} beats third-quarter estimates and raises full-year guidance", "high"),
    ("{n} upgraded to Buy at Goldman Sachs; price target raised to ${t}", "medium"),
    ("{n} wins $2 billion contract award from the Department of Defense", "high"),
    ("FDA approval granted for {n}'s lead therapy", "high"),
    ("{n} announces $5 billion share buyback program", "medium"),
]
NEG = [
    ("{n} misses revenue estimates and cuts its outlook", "high"),
    ("{n} downgraded to Underperform at Morgan Stanley", "medium"),
    ("{n} faces DOJ investigation over accounting practices", "high"),
    ("{n} prices $1 billion share offering, dilution weighs", "medium"),
    ("{n} issues recall of 200,000 units after safety probe", "medium"),
]
NEUTRAL = ["{n} to present at an investor conference next week", "Options traders watch {n} ahead of expiry"]
MOVE = {"high": 3.2, "medium": 1.6, "low": 0.7}


@dataclass
class _Event:
    sym: str
    t: datetime
    direction: int
    pct: float
    duration: float


class DemoProvider:
    name = "demo"

    def __init__(self, date: str | None = None, half_day: bool = False, seed: int | None = None):
        today = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        y, m, d = (int(x) for x in today.split("-"))
        noon = datetime(y, m, d, 17, tzinfo=timezone.utc)
        off = et_offset(noon)

        def et(h, mi):
            return datetime(y, m, d, h, mi, tzinfo=timezone.utc) - off

        self.open = et(9, 30)
        self.close = et(13, 0) if half_day else et(16, 0)
        self.now = et(8, 30)
        self.rng = random.Random(seed if seed is not None else y * 10000 + m * 100 + d)
        self.prev_close = {s: p for s, (_, p) in STOCKS.items()}
        self.price = dict(self.prev_close)
        self.price_t = et(4, 0)
        self.events: list[_Event] = []
        self.articles: list[Article] = []
        self._make_news(et(6, 0))

    def _make_news(self, start: datetime) -> None:
        rng = self.rng
        span = (self.close - timedelta(minutes=45) - start).total_seconds() / 60
        syms = list(STOCKS)
        for i in range(16):
            sym = rng.choice(syms)
            t = start + timedelta(minutes=rng.uniform(0, span))
            name = STOCKS[sym][0]
            r = rng.random()
            if r < 0.42:
                tpl, impact = rng.choice(POS)
                intended = 1
            elif r < 0.84:
                tpl, impact = rng.choice(NEG)
                intended = -1
            else:
                tpl, impact = rng.choice(NEUTRAL), "low"
                intended = rng.choice([1, -1])
            realized = intended if rng.random() < 0.7 else -intended
            pct = MOVE[impact] * rng.uniform(0.6, 1.5)
            self.events.append(_Event(sym, t, realized, pct, rng.uniform(25, 100)))
            headline = tpl.format(n=name, t=round(self.prev_close[sym] * 1.2))
            self.articles.append(Article(
                id=f"demo-{i}", headline=headline, summary=f"(demo) {headline}.", symbols=[sym],
                created_at=t, source="demo"))
        self.articles.sort(key=lambda a: a.created_at)

    # ------------------------------------------------------------------ price model
    def _advance_prices(self) -> None:
        while self.price_t + timedelta(minutes=1) <= self.now:
            self.price_t += timedelta(minutes=1)
            in_session = self.open <= self.price_t <= self.close
            for s in self.price:
                ret = self.rng.gauss(0, 0.0009 if in_session else 0.0002)
                for e in self.events:
                    if e.sym == s and e.t <= self.price_t <= e.t + timedelta(minutes=e.duration):
                        ret += e.direction * (e.pct / 100) / e.duration
                self.price[s] = round(self.price[s] * math.exp(ret), 2)

    # ------------------------------------------------------------------ provider API
    def clock(self) -> Clock:
        is_open = self.open <= self.now < self.close
        if self.now < self.open:
            nxt_open = self.open
        else:
            nxt_open = self.open + timedelta(days=1)
        return Clock(self.now, is_open, nxt_open, self.close if self.now < self.close else self.close + timedelta(days=1))

    def news(self, since: datetime, limit_pages: int = 5) -> list[Article]:
        return [Article(**vars(a)) for a in self.articles if since <= a.created_at <= self.now]

    def snapshots(self, symbols: list[str]) -> dict[str, Quote]:
        self._advance_prices()
        out = {}
        for s in symbols:
            if s in self.price:
                p = self.price[s]
                out[s] = Quote(s, p, prev_close=self.prev_close[s], bid=round(p - 0.01, 2), ask=round(p + 0.01, 2),
                               ts=self.now)
        return out

    def hot_symbols(self, top: int = 20) -> list[HotSymbol]:
        self._advance_prices()
        ranked = sorted(self.price, key=lambda s: -abs(self.price[s] / self.prev_close[s] - 1))
        return [HotSymbol(s, f"mover {(self.price[s] / self.prev_close[s] - 1) * 100:+.1f}%") for s in ranked[:6]]

    def company_names(self) -> dict[str, str]:
        return {s: name for s, (name, _) in STOCKS.items()}

    def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=max(seconds, 1))

    def wall_now(self) -> datetime:
        return self.now
