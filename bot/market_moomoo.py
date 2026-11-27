"""moomoo price source (desktop only) + optional mirror of simulated trades into a moomoo PAPER account.

Needs moomoo OpenD running and logged in on this computer (https://www.moomoo.com/download/OpenAPI).
News, the market clock and the hot-stock screener still come from Alpaca; only prices come from moomoo.
If a moomoo request fails, that minute falls back to Alpaca prices so the simulation never stalls.
"""
from __future__ import annotations

import logging
import os

from .market import AlpacaProvider, ProviderError, Quote
from .timeutil import utcnow

log = logging.getLogger(__name__)


def _num(v) -> float | None:
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


class MoomooProvider(AlpacaProvider):
    name = "moomoo"

    def __init__(self, alpaca_key: str, alpaca_secret: str, host: str = "127.0.0.1", port: int = 11111,
                 quote_ctx=None):
        super().__init__(alpaca_key, alpaca_secret)
        from moomoo import RET_OK, OpenQuoteContext

        self._ok = RET_OK
        self.quote_ctx = quote_ctx if quote_ctx is not None else OpenQuoteContext(host=host, port=port)
        self.fallback_minutes = 0

    def shutdown(self) -> None:
        try:
            self.quote_ctx.close()
        except Exception:  # noqa: BLE001
            pass

    def _moomoo_snap(self, symbols: list[str]) -> dict[str, Quote]:
        ret, df = self.quote_ctx.get_market_snapshot([f"US.{s}" for s in symbols])
        if ret != self._ok:
            raise ProviderError(f"moomoo snapshot: {df}")
        out: dict[str, Quote] = {}
        now = utcnow()
        for row in df.to_dict("records"):
            sym = str(row.get("code", "")).split(".", 1)[-1]
            price = _num(row.get("last_price"))
            if not sym or price is None:
                continue
            out[sym] = Quote(
                symbol=sym, price=price, prev_close=_num(row.get("prev_close_price")),
                day_open=_num(row.get("open_price")), bid=_num(row.get("bid_price")), ask=_num(row.get("ask_price")),
                ts=now, volume=_num(row.get("volume")), source="moomoo",
            )
        return out

    def snapshots(self, symbols: list[str]) -> dict[str, Quote]:
        syms = sorted({s for s in symbols if s and s.isascii() and s.replace(".", "").isalnum() and len(s) <= 6})
        out: dict[str, Quote] = {}
        for i in range(0, len(syms), 300):  # moomoo allows up to 400 per request
            chunk = syms[i : i + 300]
            try:
                out.update(self._moomoo_snap(chunk))
                continue
            except ProviderError as e:
                # usually one unknown ticker (OTC / foreign) rejects the whole request:
                # keep only symbols Alpaca recognises and try once more
                log.info("moomoo snapshot failed (%s); retrying with validated symbols", str(e)[:120])
            alpaca = super().snapshots(chunk)
            try:
                out.update(self._moomoo_snap(sorted(alpaca)))
            except ProviderError as e:
                self.fallback_minutes += 1
                log.warning("moomoo unavailable (%s); using Alpaca prices this minute", str(e)[:120])
                out.update(alpaca)
        return out


class MoomooPaperMirror:
    """Copies each simulated entry/exit into the moomoo PAPER account as a market order.

    Safety: the trading environment is hard-coded to SIMULATE. This class cannot place real orders.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 11111, security_firm: str | None = None, ctx=None):
        from moomoo import RET_OK, OpenSecTradeContext, OrderType, SecurityFirm, TrdEnv, TrdMarket, TrdSide

        self.RET_OK, self.OrderType, self.TrdSide = RET_OK, OrderType, TrdSide
        self.ENV = TrdEnv.SIMULATE
        assert self.ENV == "SIMULATE"
        if ctx is None:
            firm = getattr(SecurityFirm, (security_firm or os.environ.get("MOOMOO_SECURITY_FIRM") or "FUTUSG").upper())
            ctx = OpenSecTradeContext(filter_trdmarket=TrdMarket.US, host=host, port=port, security_firm=firm)
        self.ctx = ctx

    def _order(self, pos: dict, side, label: str) -> str:
        try:
            ret, data = self.ctx.place_order(
                price=pos.get("entry_price") if label == "open" else pos.get("exit_price"),
                qty=pos["shares"], code=f"US.{pos['symbol']}", trd_side=side,
                order_type=self.OrderType.MARKET, trd_env=self.ENV, remark="news-sim-bot",
            )
        except Exception as e:  # noqa: BLE001 - the mirror must never break the simulation
            return f"moomoo paper {label} error: {e}"[:200]
        if ret != self.RET_OK:
            return f"moomoo paper {label} rejected: {data}"[:200]
        try:
            oid = data["order_id"].iloc[0]
        except Exception:  # noqa: BLE001
            oid = "?"
        return f"moomoo paper order {oid} placed"

    def open(self, pos: dict) -> str:
        side = self.TrdSide.BUY if pos["side"] == "LONG" else self.TrdSide.SELL_SHORT
        return self._order(pos, side, "open")

    def close(self, pos: dict) -> str:
        side = self.TrdSide.SELL if pos["side"] == "LONG" else self.TrdSide.BUY_BACK
        return self._order(pos, side, "close")

    def shutdown(self) -> None:
        try:
            self.ctx.close()
        except Exception:  # noqa: BLE001
            pass
