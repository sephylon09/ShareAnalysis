"""Entry point.

  python -m bot.run                          # live, Alpaca prices (GitHub Actions uses this)
  python -m bot.run --prices moomoo          # live, moomoo prices via OpenD on this PC (desktop copy)
  python -m bot.run --demo                   # offline simulated day, no keys needed
  python -m bot.run --publish                # also push results to a branch for the dashboard
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from .config import Settings, load_dotenv
from .engine import Engine
from .scorer import LexiconScorer, make_scorer
from .store import Store


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="News-driven US stock paper simulation")
    ap.add_argument("--prices", choices=["alpaca", "moomoo"], default="alpaca", help="where live prices come from")
    ap.add_argument("--demo", action="store_true", help="run a simulated market day offline")
    ap.add_argument("--demo-date", help="YYYY-MM-DD for the demo day (default: today)")
    ap.add_argument("--half-day", action="store_true", help="demo: 1pm ET early close")
    ap.add_argument("--seed", type=int, help="demo: random seed")
    ap.add_argument("--data-dir", help="where JSON results are written (default: data, or data-moomoo)")
    ap.add_argument("--publish", action="store_true", help="force-push the data dir to a branch")
    ap.add_argument("--branch", help="results branch (default: data, or data-moomoo)")
    ap.add_argument("--max-runtime-min", type=float, help="stop after N minutes (next run resumes)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    load_dotenv()
    settings = Settings()
    if not args.demo and not (settings.alpaca_key and settings.alpaca_secret):
        logging.warning("ALPACA_KEY_ID / ALPACA_SECRET_KEY are not set yet. On GitHub add them under "
                        "Settings > Secrets and variables > Actions; on a PC put them in .env. Nothing to do.")
        return 0
    suffix = "-moomoo" if args.prices == "moomoo" else ""
    data_dir = args.data_dir or f"data{suffix}"
    branch = args.branch or f"data{suffix}"
    mirror = None

    if args.demo:
        from .demo import DemoProvider

        provider = DemoProvider(args.demo_date, half_day=args.half_day, seed=args.seed)
        scorer = LexiconScorer() if not settings.anthropic_key_present else make_scorer(settings)
        max_rt = args.max_runtime_min or 10_000
        settings.publish_every_min = 1e9  # virtual clock: publish once at the end only
    elif args.prices == "moomoo":
        from .market_moomoo import MoomooPaperMirror, MoomooProvider

        host = os.environ.get("MOOMOO_OPEND_HOST", "127.0.0.1")
        port = int(os.environ.get("MOOMOO_OPEND_PORT", "11111"))
        provider = MoomooProvider(settings.alpaca_key, settings.alpaca_secret, host, port)
        if os.environ.get("MOOMOO_PAPER_MIRROR", "0") == "1":
            mirror = MoomooPaperMirror(host, port)
            logging.info("Mirroring simulated trades into the moomoo PAPER account")
        scorer = make_scorer(settings)
        max_rt = args.max_runtime_min or 720
    else:
        from .market import AlpacaProvider

        provider = AlpacaProvider(settings.alpaca_key, settings.alpaca_secret)
        scorer = make_scorer(settings)
        max_rt = args.max_runtime_min

    logging.info("Prices: %s | scorer: %s | results: %s -> branch %s", provider.name,
                 getattr(scorer, "model", None) or scorer.name, data_dir, branch if args.publish else "(not published)")
    engine = Engine(settings, provider, scorer, Store(data_dir, publish_enabled=args.publish, branch=branch),
                    tag="-demo" if args.demo else "", mirror=mirror)
    try:
        engine.run(max_runtime_min=max_rt)
    finally:
        if hasattr(provider, "shutdown"):
            provider.shutdown()
        if mirror is not None:
            mirror.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
