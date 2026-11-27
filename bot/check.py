"""Connection check: confirms your keys work without trading anything.

  python -m bot.check

Reads keys from environment variables or a local .env file. Never prints the keys.
"""
from __future__ import annotations

import sys
from datetime import timedelta

from .config import Settings, load_dotenv
from .timeutil import et_hhmm, iso


def main() -> int:
    load_dotenv()
    s = Settings()
    ok = True

    def line(status: bool, msg: str) -> None:
        nonlocal ok
        ok &= status
        print(("  OK   " if status else "  FAIL ") + msg)

    print("Alpaca")
    if not (s.alpaca_key and s.alpaca_secret):
        line(False, "ALPACA_KEY_ID / ALPACA_SECRET_KEY not set (put them in .env or environment variables)")
        return 1
    from .market import AlpacaProvider, ProviderError

    p = AlpacaProvider(s.alpaca_key, s.alpaca_secret)
    try:
        c = p.clock()
        state = "OPEN" if c.is_open else "closed"
        line(True, f"market clock: {state}; next open {iso(c.next_open)} ({et_hhmm(c.next_open)} ET), "
                   f"next close {et_hhmm(c.next_close)} ET")
    except ProviderError as e:
        line(False, f"market clock: {e}  <- usually wrong keys, or live keys instead of paper keys")
        return 1
    try:
        q = p.snapshots(["AAPL", "NVDA", "TSLA"])
        line(bool(q), "prices: " + ", ".join(f"{k} ${v.price:.2f}" for k, v in sorted(q.items())))
    except ProviderError as e:
        line(False, f"prices: {e}")
    try:
        news = p.news(c.now - timedelta(hours=3), limit_pages=2)
        line(True, f"news: {len(news)} articles in the last 3 h"
                   + (f'; latest: "{news[-1].headline[:70]}"' if news else ""))
    except ProviderError as e:
        line(False, f"news: {e}")
    hot = p.hot_symbols(10)
    line(bool(hot), f"hot stocks screener: {len(hot)} symbols" + (f" (e.g. {', '.join(h.symbol for h in hot[:5])})" if hot else ""))

    print("Claude")
    if not s.anthropic_key_present:
        print("  --   ANTHROPIC_API_KEY not set: the free keyword model will be used")
    else:
        try:
            from .market import Article
            from .scorer import ClaudeScorer

            sc = ClaudeScorer(s.claude_model)
            sigs = sc.score([Article("check", "Apple beats estimates and raises full-year guidance", "",
                                     ["AAPL"], c.now)], q or {})
            used_claude = bool(sigs) and sigs[0].scored_by.startswith("claude")
            line(used_claude, f"{s.claude_model}: test headline scored "
                 + (f"RISE chance {sigs[0].p_up}% ({sigs[0].reason[:60]})" if used_claude
                    else "by the keyword fallback, so the Claude call failed (check the key / credit balance; see log above)"))
        except Exception as e:  # noqa: BLE001
            line(False, f"{s.claude_model}: {type(e).__name__}: {str(e)[:150]}")

    print("\nAll good." if ok else "\nSome checks failed (see above).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
