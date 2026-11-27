"""Market data + news provider backed by Alpaca's free API (IEX real-time feed + Benzinga news).

Only the standard library is used so the bot runs anywhere.
Docs: https://docs.alpaca.markets/docs/about-market-data-api
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime

from .timeutil import iso, parse_ts, utcnow


@dataclass
class Clock:
    now: datetime
    is_open: bool
    next_open: datetime
    next_close: datetime


@dataclass
class Article:
    id: str
    headline: str
    summary: str
    symbols: list[str]
    created_at: datetime
    source: str = ""
    url: str = ""


@dataclass
class Quote:
    symbol: str
    price: float  # latest trade price
    prev_close: float | None = None
    day_open: float | None = None
    bid: float | None = None
    ask: float | None = None
    ts: datetime | None = None
    volume: float | None = None
    source: str = ""

    @property
    def change_pct(self) -> float | None:
        if self.prev_close:
            return (self.price / self.prev_close - 1) * 100
        return None

    @property
    def spread_pct(self) -> float | None:
        if self.bid and self.ask and self.ask >= self.bid > 0:
            return (self.ask - self.bid) / ((self.ask + self.bid) / 2) * 100
        return None


@dataclass
class HotSymbol:
    symbol: str
    reason: str
    extra: dict = field(default_factory=dict)


class ProviderError(RuntimeError):
    pass


class AlpacaProvider:
    name = "alpaca"
    TRADING = "https://paper-api.alpaca.markets"
    DATA = "https://data.alpaca.markets"

    def __init__(self, key: str, secret: str, feed: str = "iex"):
        if not key or not secret:
            raise ProviderError("ALPACA_KEY_ID / ALPACA_SECRET_KEY are not set")
        self.headers = {
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
            "Accept": "application/json",
            "User-Agent": "news-sim-bot/1.0",
        }
        self.feed = feed

    # ------------------------------------------------------------------ http
    def _get(self, base: str, path: str, params: dict | None = None, retries: int = 3):
        url = base + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        last: Exception | None = None
        for attempt in range(retries):
            req = urllib.request.Request(url, headers=self.headers)
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")[:300]
                if e.code in (429, 500, 502, 503, 504):
                    last = ProviderError(f"{e.code} {path}: {body}")
                    time.sleep(2 * (attempt + 1))
                    continue
                raise ProviderError(f"{e.code} {path}: {body}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                last = e
                time.sleep(2 * (attempt + 1))
        raise ProviderError(f"GET {path} failed: {last}")

    # ----------------------------------------------------------------- api
    def clock(self) -> Clock:
        c = self._get(self.TRADING, "/v2/clock")
        return Clock(
            now=parse_ts(c["timestamp"]),
            is_open=bool(c["is_open"]),
            next_open=parse_ts(c["next_open"]),
            next_close=parse_ts(c["next_close"]),
        )

    def news(self, since: datetime, limit_pages: int = 5) -> list[Article]:
        out: list[Article] = []
        token = None
        for _ in range(limit_pages):
            data = self._get(
                self.DATA,
                "/v1beta1/news",
                {"start": iso(since), "limit": 50, "sort": "asc", "include_content": "false", "page_token": token},
            )
            for n in data.get("news", []):
                out.append(
                    Article(
                        id=str(n["id"]),
                        headline=n.get("headline", "").strip(),
                        summary=(n.get("summary") or "").strip(),
                        symbols=[s.upper() for s in n.get("symbols", [])],
                        created_at=parse_ts(n["created_at"]),
                        source=n.get("source", ""),
                        url=n.get("url", ""),
                    )
                )
            token = data.get("next_page_token")
            if not token:
                break
        return out

    def snapshots(self, symbols: list[str]) -> dict[str, Quote]:
        out: dict[str, Quote] = {}
        syms = sorted({s for s in symbols if s and s.isascii() and len(s) <= 6})
        for i in range(0, len(syms), 100):
            chunk = syms[i : i + 100]
            try:
                data = self._get(self.DATA, "/v2/stocks/snapshots", {"symbols": ",".join(chunk), "feed": self.feed})
            except ProviderError as e:
                # one bad symbol (e.g. OTC/foreign) can 400 the whole request: fall back one by one
                if "400" in str(e) or "422" in str(e):
                    data = {}
                    for s in chunk:
                        try:
                            data.update(self._get(self.DATA, "/v2/stocks/snapshots", {"symbols": s, "feed": self.feed}))
                        except ProviderError:
                            pass
                else:
                    raise
            for sym, snap in (data or {}).items():
                if not snap:
                    continue
                trade = snap.get("latestTrade") or {}
                quote = snap.get("latestQuote") or {}
                daily = snap.get("dailyBar") or {}
                prev = snap.get("prevDailyBar") or {}
                price = trade.get("p") or (snap.get("minuteBar") or {}).get("c") or daily.get("c")
                if not price:
                    continue
                out[sym] = Quote(
                    symbol=sym,
                    price=float(price),
                    prev_close=float(prev["c"]) if prev.get("c") else None,
                    day_open=float(daily["o"]) if daily.get("o") else None,
                    bid=float(quote["bp"]) if quote.get("bp") else None,
                    ask=float(quote["ap"]) if quote.get("ap") else None,
                    ts=parse_ts(trade["t"]) if trade.get("t") else None,
                    volume=float(daily["v"]) if daily.get("v") else None,
                    source="alpaca",
                )
        return out

    def hot_symbols(self, top: int = 20) -> list[HotSymbol]:
        out: list[HotSymbol] = []
        try:
            act = self._get(self.DATA, "/v1beta1/screener/stocks/most-actives", {"by": "volume", "top": top})
            for a in act.get("most_actives", []):
                out.append(HotSymbol(a["symbol"], "most active by volume", {"volume": a.get("volume")}))
        except ProviderError:
            pass
        try:
            mov = self._get(self.DATA, "/v1beta1/screener/stocks/movers", {"top": top})
            for g in mov.get("gainers", []):
                out.append(HotSymbol(g["symbol"], f"top gainer {g.get('percent_change', 0):+.1f}%"))
            for l in mov.get("losers", []):
                out.append(HotSymbol(l["symbol"], f"top loser {l.get('percent_change', 0):+.1f}%"))
        except ProviderError:
            pass
        return out

    def sleep(self, seconds: float) -> None:
        time.sleep(max(0.0, seconds))

    def wall_now(self) -> datetime:
        return utcnow()
