"""Entry point.

  python -m bot.run                 # live (needs ALPACA_KEY_ID / ALPACA_SECRET_KEY; ANTHROPIC_API_KEY optional)
  python -m bot.run --demo          # offline simulated day, no keys needed
  python -m bot.run --publish       # also push results to the `data` branch (used by GitHub Actions)
"""
from __future__ import annotations

import argparse
import logging
import sys

from .config import Settings
from .engine import Engine
from .scorer import LexiconScorer, make_scorer
from .store import Store


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="News-driven US stock paper simulation")
    ap.add_argument("--demo", action="store_true", help="run a simulated market day offline")
    ap.add_argument("--demo-date", help="YYYY-MM-DD for the demo day (default: today)")
    ap.add_argument("--half-day", action="store_true", help="demo: 1pm ET early close")
    ap.add_argument("--seed", type=int, help="demo: random seed")
    ap.add_argument("--data-dir", default="data", help="where JSON results are written")
    ap.add_argument("--publish", action="store_true", help="force-push data dir to the `data` branch")
    ap.add_argument("--max-runtime-min", type=float, help="stop after N minutes (next run resumes)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    settings = Settings()

    if args.demo:
        from .demo import DemoProvider

        provider = DemoProvider(args.demo_date, half_day=args.half_day, seed=args.seed)
        scorer = LexiconScorer() if not settings.anthropic_key_present else make_scorer(settings)
        max_rt = args.max_runtime_min or 10_000
        settings.publish_every_min = 1e9  # virtual clock: publish once at the end only
    else:
        from .market import AlpacaProvider

        provider = AlpacaProvider(settings.alpaca_key, settings.alpaca_secret)
        scorer = make_scorer(settings)
        max_rt = args.max_runtime_min

    logging.info("Scorer: %s", getattr(scorer, "model", None) or scorer.name)
    engine = Engine(settings, provider, scorer, Store(args.data_dir, publish_enabled=args.publish),
                    tag="-demo" if args.demo else "")
    engine.run(max_runtime_min=max_rt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
