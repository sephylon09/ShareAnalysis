"""The trading-simulation engine: news -> signals -> picks -> paper entries/exits -> end-of-day grading."""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta

from . import sim
from .config import Settings
from .market import Article, Clock, Quote
from .scorer import IMPACT_WEIGHT, LexiconScorer, Signal
from .store import Store
from .timeutil import et_date, et_hhmm, iso, minutes_between, parse_ts

log = logging.getLogger(__name__)


def call_of(p_up: float) -> str:
    return "RISE" if p_up >= 50 else "FALL"


def confidence_of(p_up: float) -> int:
    """Chance (50-99) of the predicted direction."""
    return int(round(max(p_up, 100 - p_up)))


class Engine:
    def __init__(self, settings: Settings, provider, scorer, store: Store, tag: str = ""):
        self.tag = tag  # "-demo" keeps demo days separate from live days
        self.s = settings
        self.p = provider
        self.scorer = scorer
        self.keyword_scorer = LexiconScorer()
        self.store = store
        self.day: dict | None = None
        self.history: dict[str, deque] = defaultdict(lambda: deque(maxlen=60))  # sym -> (time, price)
        self.hot: dict[str, str] = {}
        self.hot_refreshed: datetime | None = None
        self.last_publish: datetime | None = None
        self.quotes: dict[str, Quote] = {}

    # ================================================================== lifecycle
    def run(self, max_runtime_min: float | None = None) -> None:
        max_runtime_min = max_runtime_min or self.s.max_runtime_min
        started = self.p.wall_now()
        while True:
            clk = self.p.clock()
            now = clk.now
            if clk.is_open:
                self._ensure_day(now, clk.next_close, session_open=None)
                self.cycle(clk, trading=True)
            else:
                mins_to_open = minutes_between(now, clk.next_open)
                today = et_date(now)
                existing = self.store.load_day(today + self.tag) if self.day is None else self.day
                if existing and existing.get("session", {}).get("close") and not existing.get("finalized") \
                        and now >= parse_ts(existing["session"]["close"]):
                    self.day = existing
                    self.finalize(now)
                    self.publish(force=True)
                    log.info("Session finished and graded. Exiting.")
                    return
                if mins_to_open <= self.s.premarket_window_min and et_date(clk.next_open) == today:
                    self._ensure_day(now, None, session_open=clk.next_open)
                    self.cycle(clk, trading=False)
                else:
                    log.info("Market closed; next open %s (%.0f min away). Nothing to do.", iso(clk.next_open), mins_to_open)
                    self.store.write_latest({"status": "closed", "updated_at": iso(now), "next_open": iso(clk.next_open),
                                             "day": self.store.latest_day()})
                    self.publish(force=True)
                    return
            if minutes_between(started, self.p.wall_now()) >= max_runtime_min:
                log.info("Max runtime reached; saving and handing over to the next scheduled run.")
                self.save()
                self.publish(force=True)
                return
            self.p.sleep(self.s.poll_seconds)

    def _ensure_day(self, now: datetime, session_close: datetime | None, session_open: datetime | None) -> None:
        today = et_date(now)
        if self.day is None or self.day["date"] != today:
            self.day = self.store.load_day(today + self.tag) or self._new_day(today, now)
        sess = self.day["session"]
        if session_close is not None and not sess.get("close"):
            sess["close"] = iso(session_close)
            sess["half_day"] = et_hhmm(session_close) < "16:00"
        if session_open is not None and not sess.get("open"):
            sess["open"] = iso(session_open)
        if session_close is not None and not sess.get("open"):
            # bot started after the open: assume the regular 9:30 ET open
            sess["open"] = iso(self._et_930(now))

    @staticmethod
    def _et_930(now: datetime) -> datetime:
        from .timeutil import et_offset
        local = now + et_offset(now)
        local_930 = local.replace(hour=9, minute=30, second=0, microsecond=0)
        return local_930 - et_offset(now)

    def _new_day(self, today: str, now: datetime) -> dict:
        return {
            "id": today + self.tag,
            "date": today,
            "demo": bool(self.tag),
            "created_at": iso(now),
            "session": {"open": None, "close": None, "half_day": False},
            "status": "premarket",
            "scorer": getattr(self.scorer, "name", "keywords") if not hasattr(self.scorer, "model")
                      else f"claude ({self.scorer.model})",
            "rules": self._rules_dict(),
            "news_cursor": iso(now - timedelta(hours=16)),  # pick up overnight news on the first run
            "seen_article_ids": [],
            "signals": [],
            "predictions": [],
            "picks": [],
            "positions": [],
            "trades": [],
            "skips": [],
            "price_paths": {},
            "last_prices": {},
            "llm": {"articles_scored": 0, "input_tokens": 0, "output_tokens": 0},
            "finalized": False,
            "summary": {},
        }

    def _rules_dict(self) -> dict:
        s = self.s
        return {
            "position_usd": s.position_usd, "slippage": s.slippage, "entry_confidence": s.entry_conf,
            "stop_loss_pct": s.stop_loss_pct, "breakeven_trigger_pct": s.breakeven_trigger_pct,
            "trail_trigger_pct": s.trail_trigger_pct, "trail_pct": s.trail_pct, "take_profit_pct": s.take_profit_pct,
            "force_exit_before_close_min": s.force_exit_before_close_min,
            "no_entry_before_close_min": s.no_entry_before_close_min, "fresh_news_min": s.fresh_news_min,
            "max_open_positions": s.max_open_positions, "max_trades_per_day": s.max_trades_per_day,
        }

    # ================================================================== one loop
    def cycle(self, clk: Clock, trading: bool) -> None:
        now = clk.now
        d = self.day
        d["status"] = "open" if trading else "premarket"
        self._refresh_hot(now)
        articles = self._fetch_news(now)
        tracked = set(self.s.watchlist) | set(self.hot) | {p["symbol"] for p in d["positions"]}
        tracked |= {sig["symbol"] for sig in d["signals"][-300:]}
        news_syms = {s for a in articles for s in a.symbols}
        self.quotes = self.p.snapshots(sorted(tracked | news_syms))
        for sym, q in self.quotes.items():
            self.history[sym].append((now, q.price))
            if trading:
                d["last_prices"][sym] = [iso(now), q.price]
        self._score(articles, now)
        agg = self.aggregate(now)
        self._update_predictions(now, agg)
        if trading:
            self._manage_exits(now, clk, agg)
            self._consider_entries(now, clk, agg)
            self._record_paths(now)
        self._update_picks(now, agg)
        d["updated_at"] = iso(now)
        self.save()
        self.publish()

    # ------------------------------------------------------------------ inputs
    def _refresh_hot(self, now: datetime) -> None:
        if self.hot_refreshed and minutes_between(self.hot_refreshed, now) < 5:
            return
        hot = {}
        for h in self.p.hot_symbols(20):
            hot.setdefault(h.symbol, h.reason)
        self.hot = hot
        self.hot_refreshed = now

    def _fetch_news(self, now: datetime) -> list[Article]:
        d = self.day
        since = parse_ts(d["news_cursor"]) - timedelta(minutes=2)
        seen = set(d["seen_article_ids"])
        fresh = [a for a in self.p.news(since) if a.id not in seen and a.created_at <= now]
        if fresh:
            d["news_cursor"] = iso(max(a.created_at for a in fresh))
            d["seen_article_ids"].extend(a.id for a in fresh)
            d["seen_article_ids"] = d["seen_article_ids"][-3000:]
        # keep the articles worth analysing: few tickers, or tickers we care about
        focus = set(self.s.watchlist) | set(self.hot) | {p["symbol"] for p in d["positions"]}
        keep = []
        for a in fresh:
            syms = a.symbols if len(a.symbols) <= self.s.max_symbols_per_article else [s for s in a.symbols if s in focus]
            if syms and a.headline:
                a.symbols = syms
                keep.append(a)
        return keep

    def _score(self, articles: list[Article], now: datetime) -> None:
        d = self.day
        tradable = {s: q for s, q in self.quotes.items() if q.price >= self.s.min_price}
        articles = [a for a in articles if any(s in tradable for s in a.symbols)]
        if not articles:
            return
        budget_left = self.s.llm_max_articles_per_day - d["llm"]["articles_scored"]
        use_llm = articles[: max(0, budget_left)] if hasattr(self.scorer, "model") else []
        rest = articles[len(use_llm):] if use_llm else articles
        sigs: list[Signal] = []
        if use_llm:
            sigs += self.scorer.score(use_llm, tradable)
            d["llm"]["articles_scored"] += len(use_llm)
            d["llm"]["input_tokens"] = getattr(self.scorer, "input_tokens", 0)
            d["llm"]["output_tokens"] = getattr(self.scorer, "output_tokens", 0)
        if rest:
            sigs += self.keyword_scorer.score(rest, tradable)
        for sg in sigs:
            row = sg.to_dict()
            row["scored_at"] = iso(now)
            d["signals"].append(row)
        log.info("Scored %d articles -> %d signals", len(articles), len(sigs))

    # ------------------------------------------------------------------ aggregation
    def aggregate(self, now: datetime) -> dict[str, dict]:
        """Combine all of today's signals per symbol, recent + high-impact news weighing most."""
        by_sym: dict[str, list[dict]] = defaultdict(list)
        for sg in self.day["signals"]:
            by_sym[sg["symbol"]].append(sg)
        out = {}
        for sym, sigs in by_sym.items():
            num = wsum = 0.0
            latest = None
            top = None
            for sg in sigs:
                age = max(0.0, minutes_between(parse_ts(sg["published_at"]), now))
                w = IMPACT_WEIGHT.get(sg["impact"], 0.5) * 0.5 ** (age / 60.0)
                num += w * (sg["p_up"] - 50)
                wsum += w
                t = parse_ts(sg["published_at"])
                latest = t if latest is None or t > latest else latest
                if top is None or w * abs(sg["p_up"] - 50) > top[0]:
                    top = (w * abs(sg["p_up"] - 50), sg)
            if wsum <= 0:
                continue
            p_up = 50 + (num / wsum) * min(1.0, wsum)
            out[sym] = {
                "p_up": round(p_up, 1),
                "n": len(sigs),
                "latest": latest,
                "top": top[1] if top else None,
            }
        return out

    def _momentum(self, sym: str, now: datetime, minutes: float = 5) -> tuple[float | None, int]:
        h = self.history.get(sym)
        if not h or len(h) < 3:
            return None, len(h or [])
        ref = None
        for t, p in h:
            if minutes_between(t, now) <= minutes:
                ref = p
                break
        ref = ref if ref is not None else h[0][1]
        return (h[-1][1] / ref - 1) * 100, len(h)

    # ------------------------------------------------------------------ predictions
    def _update_predictions(self, now: datetime, agg: dict) -> None:
        d = self.day
        last_call = {}
        for pr in d["predictions"]:
            last_call[pr["symbol"]] = pr["call"]
        for sym, a in agg.items():
            conf = confidence_of(a["p_up"])
            q = self.quotes.get(sym)
            if conf < self.s.pick_conf or not q or q.price < self.s.min_price:
                continue
            call = call_of(a["p_up"])
            if last_call.get(sym) == call:
                continue
            top = a["top"] or {}
            d["predictions"].append({
                "time": iso(now), "symbol": sym, "call": call, "confidence": conf, "p_up": a["p_up"],
                "price": q.price, "headline": top.get("headline", ""), "reason": top.get("reason", ""),
                "session": d["status"], "close_price": None, "move_pct": None, "correct": None,
            })

    # ------------------------------------------------------------------ exits
    def _manage_exits(self, now: datetime, clk: Clock, agg: dict) -> None:
        d = self.day
        s = self.s
        mins_left = minutes_between(now, clk.next_close)
        still_open = []
        for pos in d["positions"]:
            q = self.quotes.get(pos["symbol"])
            if not q:
                still_open.append(pos)
                continue
            side = pos["side"]
            live = sim.live_pnl_pct(side, pos["entry_price"], q.price, s.slippage)
            pos["peak_pnl_pct"] = max(pos.get("peak_pnl_pct", live), live)
            pos["last_price"] = q.price
            pos["live_pnl_pct"] = live
            peak = pos["peak_pnl_pct"]
            held = minutes_between(parse_ts(pos["entry_time"]), now)
            a = agg.get(pos["symbol"])
            reason = None
            if live <= -s.stop_loss_pct:
                reason = f"Stop-loss: down {live:.2f}% (limit -{s.stop_loss_pct}%). Cut the loss before it grows."
            elif peak >= s.trail_trigger_pct and live <= peak - s.trail_pct:
                reason = (f"Trailing stop: peaked at +{peak:.2f}%, gave back {peak - live:.2f}% "
                          f"-> locked +{live:.2f}%.")
            elif peak >= s.breakeven_trigger_pct and live <= s.breakeven_floor_pct:
                reason = (f"Break-even protection: was up +{peak:.2f}%, slipped back to {live:+.2f}%. "
                          f"Exit so a winner never turns into a loser.")
            elif live >= s.take_profit_pct:
                reason = f"Take-profit: +{live:.2f}% reached the +{s.take_profit_pct}% target."
            elif a and a["latest"] and a["latest"] > parse_ts(pos["entry_time"]) and (
                    (side == "LONG" and a["p_up"] <= s.reversal_p) or (side == "SHORT" and a["p_up"] >= 100 - s.reversal_p)):
                reason = (f"News reversal: new headline flipped the combined call to {call_of(a['p_up'])} "
                          f"{confidence_of(a['p_up'])}% ('{(a['top'] or {}).get('headline', '')[:90]}').")
            elif held >= s.stall_minutes and 0 < live < 0.5:
                reason = f"Stalled: {held:.0f} min with only {live:+.2f}%. Take the small gain, free the capital."
            elif mins_left <= s.force_exit_before_close_min:
                reason = f"End of day: {mins_left:.0f} min to close, never hold overnight. Exit at {live:+.2f}%."
            if reason:
                self._close(pos, now, q.price, reason)
            else:
                still_open.append(pos)
        d["positions"] = still_open

    def _close(self, pos: dict, now: datetime, market_price: float, reason: str) -> None:
        fill = sim.exit_fill(pos["side"], market_price, self.s.slippage)
        usd, pct = sim.pnl(pos["side"], pos["entry_price"], fill, pos["shares"])
        pos.update({
            "exit_time": iso(now), "exit_market_price": market_price, "exit_price": fill,
            "exit_reason": reason, "pnl_usd": usd, "pnl_pct": pct,
            "held_min": round(minutes_between(parse_ts(pos["entry_time"]), now), 1),
        })
        self.day["trades"].append(pos)
        log.info("EXIT %s %s @ %.2f  %+.2f USD (%s)", pos["side"], pos["symbol"], fill, usd, reason)

    # ------------------------------------------------------------------ entries
    def _skip(self, now: datetime, sym: str, why: str) -> None:
        skips = self.day["skips"]
        if any(x["symbol"] == sym and x["why"] == why for x in skips[-40:]):
            return
        skips.append({"time": iso(now), "symbol": sym, "why": why})
        self.day["skips"] = skips[-200:]

    def _consider_entries(self, now: datetime, clk: Clock, agg: dict) -> None:
        d = self.day
        s = self.s
        open_dt = parse_ts(d["session"]["open"]) if d["session"].get("open") else None
        since_open = minutes_between(open_dt, now) if open_dt else 999
        mins_left = minutes_between(now, clk.next_close)
        traded = {t["symbol"] for t in d["trades"]} | {p["symbol"] for p in d["positions"]}
        n_trades = len(d["trades"]) + len(d["positions"])
        cands = sorted(agg.items(), key=lambda kv: -abs(kv[1]["p_up"] - 50))
        for sym, a in cands:
            conf = confidence_of(a["p_up"])
            if conf < s.entry_conf:
                break  # sorted: nothing stronger left
            side = "LONG" if a["p_up"] >= 50 else "SHORT"
            q = self.quotes.get(sym)
            if sym in traded:
                continue
            if not q or q.price < s.min_price:
                continue
            if since_open < s.entry_start_delay_min:
                self._skip(now, sym, f"waiting {s.entry_start_delay_min:.0f} min after the open for prices to settle")
                continue
            if mins_left < s.no_entry_before_close_min:
                self._skip(now, sym, f"too close to the close ({mins_left:.0f} min left)")
                continue
            age = minutes_between(a["latest"], now)
            overnight_ok = open_dt is not None and a["latest"] < open_dt and since_open <= 60
            if age > s.fresh_news_min and not overnight_ok:
                self._skip(now, sym, f"news is {age:.0f} min old - likely priced in")
                continue
            if len(d["positions"]) >= s.max_open_positions:
                self._skip(now, sym, f"already holding {s.max_open_positions} positions")
                continue
            if n_trades >= s.max_trades_per_day:
                self._skip(now, sym, f"daily trade limit ({s.max_trades_per_day}) reached")
                continue
            chg = q.change_pct
            if chg is not None and ((side == "LONG" and chg > s.max_chase_pct) or (side == "SHORT" and chg < -s.max_chase_pct)):
                self._skip(now, sym, f"already moved {chg:+.1f}% today - not chasing")
                continue
            if q.spread_pct is not None and q.spread_pct > s.max_spread_pct:
                self._skip(now, sym, f"spread too wide ({q.spread_pct:.2f}%)")
                continue
            mom, samples = self._momentum(sym, now)
            if mom is None:
                self._skip(now, sym, "collecting a few minutes of live prices to confirm the move")
                continue
            if (side == "LONG" and mom < -s.against_momentum_pct) or (side == "SHORT" and mom > s.against_momentum_pct):
                self._skip(now, sym, f"price moving against the news ({mom:+.2f}% last 5 min) - waiting")
                continue
            self._open(now, clk, sym, side, a, q, mom)
            traded.add(sym)
            n_trades += 1

    def _open(self, now: datetime, clk: Clock, sym: str, side: str, a: dict, q: Quote, mom: float) -> None:
        s = self.s
        fill = sim.entry_fill(side, q.price, s.slippage)
        shares = sim.shares_for(s.position_usd, fill)
        top = a["top"] or {}
        conf = confidence_of(a["p_up"])
        why = [
            f"News: \"{top.get('headline', '')}\"",
            f"Analysis ({top.get('scored_by', '?')}): {top.get('reason', '')}",
            f"Combined call from {a['n']} article(s): {call_of(a['p_up'])} {conf}% (entry needs >= {s.entry_conf:.0f}%)",
            (f"Price confirms: {mom:+.2f}% over the last 5 min, in the news direction"
             if (mom >= 0.05 if side == "LONG" else mom <= -0.05)
             else f"Price not moving against the news ({mom:+.2f}% last 5 min, limit {self.s.against_momentum_pct}%)"),
        ]
        if q.change_pct is not None:
            why.append(f"Today so far: {q.change_pct:+.2f}% (not over-extended)")
        pos = {
            "id": f"{self.day['date']}-{sym}",
            "symbol": sym, "side": side, "shares": shares,
            "entry_time": iso(now), "entry_market_price": q.price, "entry_price": fill,
            "entry_p_up": a["p_up"], "confidence": conf, "headline": top.get("headline", ""),
            "entry_reason": why,
            "plan": {
                "stop_price": sim.price_for_pnl(side, fill, -s.stop_loss_pct, s.slippage),
                "breakeven_arm_price": sim.price_for_pnl(side, fill, s.breakeven_trigger_pct, s.slippage),
                "trail_arm_price": sim.price_for_pnl(side, fill, s.trail_trigger_pct, s.slippage),
                "target_price": sim.price_for_pnl(side, fill, s.take_profit_pct, s.slippage),
                "latest_exit_time": iso(clk.next_close - timedelta(minutes=s.force_exit_before_close_min)),
            },
            "peak_pnl_pct": sim.live_pnl_pct(side, fill, q.price, s.slippage),
        }
        self.day["positions"].append(pos)
        self.day["price_paths"].setdefault(sym, [])
        log.info("ENTER %s %s x%d @ %.2f (%s %d%%)", side, sym, shares, fill, call_of(a["p_up"]), conf)

    def _record_paths(self, now: datetime) -> None:
        paths = self.day["price_paths"]
        for sym in paths:
            q = self.quotes.get(sym)
            if q:
                if not paths[sym] or paths[sym][-1][0] != iso(now):
                    paths[sym].append([iso(now), q.price])

    # ------------------------------------------------------------------ picks
    def _update_picks(self, now: datetime, agg: dict) -> None:
        d = self.day
        status = {}
        for t in d["trades"]:
            status[t["symbol"]] = f"traded {t['side'].lower()}: {t['pnl_usd']:+.2f} USD"
        for p in d["positions"]:
            status[p["symbol"]] = f"holding {p['side'].lower()} ({p.get('live_pnl_pct', 0):+.2f}%)"
        last_skip = {x["symbol"]: x["why"] for x in d["skips"]}
        picks = []
        for sym, a in agg.items():
            q = self.quotes.get(sym)
            conf = confidence_of(a["p_up"])
            if conf < self.s.pick_conf or not q or q.price < self.s.min_price:
                continue
            top = a["top"] or {}
            picks.append({
                "symbol": sym, "call": call_of(a["p_up"]), "confidence": conf, "p_up": a["p_up"],
                "price": q.price, "change_pct": round(q.change_pct, 2) if q.change_pct is not None else None,
                "articles": a["n"], "headline": top.get("headline", ""), "reason": top.get("reason", ""),
                "news_time": iso(a["latest"]), "hot": self.hot.get(sym),
                "status": status.get(sym) or (f"watching - {last_skip[sym]}" if sym in last_skip else "watching"),
            })
        picks.sort(key=lambda x: -x["confidence"])
        d["picks"] = picks[:25]

    # ------------------------------------------------------------------ end of day
    def finalize(self, now: datetime) -> None:
        d = self.day
        close_prices = {s: v[1] for s, v in d["last_prices"].items()}
        for pos in list(d["positions"]):  # safety: should be empty thanks to the end-of-day rule
            px = close_prices.get(pos["symbol"], pos.get("last_price", pos["entry_market_price"]))
            self._close(pos, parse_ts(d["session"]["close"]), px, "Session ended while holding (forced flat at close).")
        d["positions"] = []
        for pr in d["predictions"]:
            cp = close_prices.get(pr["symbol"])
            if cp is None:
                continue
            pr["close_price"] = cp
            pr["move_pct"] = round((cp / pr["price"] - 1) * 100, 2)
            pr["correct"] = (cp > pr["price"]) if pr["call"] == "RISE" else (cp < pr["price"])
        for t in d["trades"]:
            cp = close_prices.get(t["symbol"])
            if cp is None:
                continue
            t["close_price"] = cp
            _, hold_pct = sim.pnl(t["side"], t["entry_price"], sim.exit_fill(t["side"], cp, self.s.slippage), 1)
            t["hold_to_close_pnl_pct"] = hold_pct
            t["call_correct_at_close"] = (cp > t["entry_market_price"]) if t["side"] == "LONG" else (cp < t["entry_market_price"])
            t["went_as_planned"] = t["pnl_usd"] > 0
        d["summary"] = self.summarize()
        d["status"] = "closed"
        d["finalized"] = True
        d["updated_at"] = iso(now)
        self.save()

    def summarize(self) -> dict:
        d = self.day
        trades = d["trades"]
        wins = [t for t in trades if t["pnl_usd"] > 0]
        graded = [p for p in d["predictions"] if p["correct"] is not None]
        return {
            "trades": len(trades),
            "wins": len(wins),
            "losses": len([t for t in trades if t["pnl_usd"] < 0]),
            "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else None,
            "pnl_usd": round(sum(t["pnl_usd"] for t in trades), 2),
            "capital_per_trade": self.s.position_usd,
            "predictions": len(d["predictions"]),
            "predictions_correct": len([p for p in graded if p["correct"]]),
            "prediction_accuracy": round(len([p for p in graded if p["correct"]]) / len(graded) * 100, 1) if graded else None,
            "best": max(trades, key=lambda t: t["pnl_usd"])["symbol"] if trades else None,
            "worst": min(trades, key=lambda t: t["pnl_usd"])["symbol"] if trades else None,
        }

    # ------------------------------------------------------------------ io
    def save(self) -> None:
        if not self.day:
            return
        d = self.day
        if not d.get("finalized"):
            d["summary"] = self.summarize()
            open_usd = 0.0
            for p in d["positions"]:
                if "live_pnl_pct" in p:
                    open_usd += p["live_pnl_pct"] / 100 * p["entry_price"] * p["shares"]
            d["summary"]["open_pnl_usd"] = round(open_usd, 2)
        self.store.save_day(d)
        self.store.write_latest({"status": d["status"], "updated_at": d.get("updated_at"), "day": d["id"],
                                 "session": d["session"]})

    def publish(self, force: bool = False) -> None:
        now = self.p.wall_now()
        if not force and self.last_publish and minutes_between(self.last_publish, now) < self.s.publish_every_min:
            return
        self.store.publish()
        self.last_publish = now
