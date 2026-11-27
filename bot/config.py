"""All tunable settings. Every value can be overridden with an environment variable."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    """Load KEY=VALUE lines from a local .env file (for running on your PC). Real env vars win."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and not os.environ.get(key):
            os.environ[key] = value


def _f(name: str, default: float) -> float:
    v = os.environ.get(name)
    return float(v) if v not in (None, "") else default


def _i(name: str, default: int) -> int:
    v = os.environ.get(name)
    return int(v) if v not in (None, "") else default


def _s(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


DEFAULT_WATCHLIST = (
    "AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,AMD,AVGO,NFLX,PLTR,COIN,MU,INTC,SMCI,"
    "BA,JPM,UBER,SHOP,CRWD,MSTR,HOOD,SOFI,LLY,UNH,DIS,WMT,COST,ORCL,ARM"
)


@dataclass
class Settings:
    # --- credentials (read from env / GitHub secrets) ---
    alpaca_key: str = field(default_factory=lambda: _s("ALPACA_KEY_ID", ""))
    alpaca_secret: str = field(default_factory=lambda: _s("ALPACA_SECRET_KEY", ""))
    anthropic_key_present: bool = field(default_factory=lambda: bool(os.environ.get("ANTHROPIC_API_KEY")))
    claude_model: str = field(default_factory=lambda: _s("CLAUDE_MODEL", "claude-haiku-5-5"))

    # --- simulation sizing ---
    position_usd: float = field(default_factory=lambda: _f("POSITION_USD", 10_000))
    slippage: float = field(default_factory=lambda: _f("SLIPPAGE", 0.01))  # $0.01 against us on every fill
    max_open_positions: int = field(default_factory=lambda: _i("MAX_OPEN_POSITIONS", 5))
    max_trades_per_day: int = field(default_factory=lambda: _i("MAX_TRADES_PER_DAY", 12))
    min_price: float = field(default_factory=lambda: _f("MIN_PRICE", 5.0))

    # --- entry rules ---
    entry_conf: float = field(default_factory=lambda: _f("ENTRY_CONFIDENCE", 70))  # p_up>=70 long, <=30 short
    pick_conf: float = field(default_factory=lambda: _f("PICK_CONFIDENCE", 62))  # shown as a "pick" on dashboard
    fresh_news_min: float = field(default_factory=lambda: _f("FRESH_NEWS_MIN", 45))
    entry_start_delay_min: float = field(default_factory=lambda: _f("ENTRY_START_DELAY_MIN", 5))
    no_entry_before_close_min: float = field(default_factory=lambda: _f("NO_ENTRY_BEFORE_CLOSE_MIN", 30))
    max_chase_pct: float = field(default_factory=lambda: _f("MAX_CHASE_PCT", 15))
    against_momentum_pct: float = field(default_factory=lambda: _f("AGAINST_MOMENTUM_PCT", 0.4))
    max_spread_pct: float = field(default_factory=lambda: _f("MAX_SPREAD_PCT", 1.0))

    # --- exit rules (all decided live, never with hindsight) ---
    stop_loss_pct: float = field(default_factory=lambda: _f("STOP_LOSS_PCT", 1.5))
    breakeven_trigger_pct: float = field(default_factory=lambda: _f("BREAKEVEN_TRIGGER_PCT", 0.8))
    breakeven_floor_pct: float = field(default_factory=lambda: _f("BREAKEVEN_FLOOR_PCT", 0.1))
    trail_trigger_pct: float = field(default_factory=lambda: _f("TRAIL_TRIGGER_PCT", 1.5))
    trail_pct: float = field(default_factory=lambda: _f("TRAIL_PCT", 0.7))
    take_profit_pct: float = field(default_factory=lambda: _f("TAKE_PROFIT_PCT", 4.0))
    stall_minutes: float = field(default_factory=lambda: _f("STALL_MINUTES", 120))
    reversal_p: float = field(default_factory=lambda: _f("REVERSAL_P", 45))
    force_exit_before_close_min: float = field(default_factory=lambda: _f("FORCE_EXIT_BEFORE_CLOSE_MIN", 10))

    # --- loop / cost control ---
    poll_seconds: float = field(default_factory=lambda: _f("POLL_SECONDS", 60))
    premarket_window_min: float = field(default_factory=lambda: _f("PREMARKET_WINDOW_MIN", 90))
    max_runtime_min: float = field(default_factory=lambda: _f("MAX_RUNTIME_MIN", 340))
    publish_every_min: float = field(default_factory=lambda: _f("PUBLISH_EVERY_MIN", 3))
    llm_max_articles_per_day: int = field(default_factory=lambda: _i("LLM_MAX_ARTICLES_PER_DAY", 600))
    max_symbols_per_article: int = field(default_factory=lambda: _i("MAX_SYMBOLS_PER_ARTICLE", 4))
    watchlist: list[str] = field(
        default_factory=lambda: [s.strip().upper() for s in _s("WATCHLIST", DEFAULT_WATCHLIST).split(",") if s.strip()]
    )
