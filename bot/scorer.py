"""Turn news articles into per-symbol direction calls.

p_up = estimated chance (1-99) that the stock closes today ABOVE its price when the news came out.
p_up 80 -> "Rise, 80%"; p_up 20 -> "Fall, 80%".

Two scorers:
  * ClaudeScorer  - used when ANTHROPIC_API_KEY is set (much better at nuance / "already priced in").
  * LexiconScorer - free keyword model, used as fallback or when no key is configured.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass

from .market import Article, Quote
from .timeutil import iso

log = logging.getLogger(__name__)

IMPACT_WEIGHT = {"low": 0.5, "medium": 1.0, "high": 2.0}


@dataclass
class Signal:
    article_id: str
    symbol: str
    headline: str
    p_up: int
    impact: str
    reason: str
    published_at: str
    scored_by: str
    price_at_news: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _clamp_p(v) -> int:
    try:
        return max(1, min(99, int(round(float(v)))))
    except (TypeError, ValueError):
        return 50


# --------------------------------------------------------------------------- lexicon
_POS = {
    r"\bbeats?\b": 2, r"\btops? (estimates|expectations)\b": 2, r"\braises? (guidance|outlook|forecast)\b": 3,
    r"\bupgrade[sd]?\b": 2, r"\brecord (revenue|sales|profit)\b": 2, r"\bfda approv": 3, r"\bapproval\b": 1,
    r"\bbuyback|repurchase\b": 1, r"\bpartnership|partners with\b": 1, r"\bcontract (win|award)|awarded\b": 2,
    r"\bacquire[sd]? by|to be acquired|takeover bid|buyout\b": 3, r"\bsurges?|soars?|jumps?|rall(y|ies)\b": 1,
    r"\bstrong demand|better[- ]than[- ]expected\b": 2, r"\bprice target (raised|hike)|raises price target\b": 2,
    r"\bdividend (hike|increase)\b": 1, r"\bbreakthrough\b": 1, r"\bexpands?\b": 0.5, r"\boutperform\b": 1,
}
_NEG = {
    r"\bmiss(es|ed)?\b": 2, r"\bcuts? (guidance|outlook|forecast)|lowers? (guidance|outlook)\b": 3,
    r"\bdowngrade[sd]?\b": 2, r"\blawsuit|sued|probe|investigation|subpoena\b": 2, r"\brecall\b": 2,
    r"\bfda (rejects|declines)|complete response letter|crl\b": 3, r"\blayoffs?|job cuts\b": 1,
    r"\bplunges?|tumbles?|sinks?|slumps?|falls?|drops?\b": 1, r"\bweak (demand|guidance|sales)\b": 2,
    r"\bworse[- ]than[- ]expected\b": 2, r"\boffering|dilution|share sale\b": 2, r"\bbankruptcy|chapter 11\b": 3,
    r"\bprice target (cut|lowered)|lowers price target\b": 2, r"\bresigns?|steps down\b": 1, r"\bdelay(s|ed)?\b": 1,
    r"\bhalt(s|ed)?\b": 1, r"\bshort seller|short report\b": 2, r"\bunderperform|sell rating\b": 2,
    r"\btariffs?\b": 0.5, r"\bdata breach|hack(ed)?\b": 1.5,
}


class LexiconScorer:
    name = "keywords"

    def score(self, articles: list[Article], quotes: dict[str, Quote],
              names: dict[str, str] | None = None) -> list[Signal]:
        out: list[Signal] = []
        for a in articles:
            text = f"{a.headline}. {a.summary}".lower()
            pos_hits = [m.group(0) for pat in _POS if (m := re.search(pat, text))]
            neg_hits = [m.group(0) for pat in _NEG if (m := re.search(pat, text))]
            pos = sum(w for pat, w in _POS.items() if re.search(pat, text))
            neg = sum(w for pat, w in _NEG.items() if re.search(pat, text))
            net = pos - neg
            if net == 0:
                continue
            magnitude = min(abs(net), 5)
            p = 50 + (1 if net > 0 else -1) * (8 + magnitude * 6)  # 1 pt -> 64/36, 5 pts -> 88/12
            impact = "high" if magnitude >= 3 else "medium" if magnitude >= 2 else "low"
            parts = []
            if pos_hits:
                parts.append("bullish: " + ", ".join(pos_hits[:4]))
            if neg_hits:
                parts.append("bearish: " + ", ".join(neg_hits[:4]))
            reason = "keyword model - " + "; ".join(parts)
            for sym in a.symbols:
                if sym not in quotes:
                    continue
                out.append(
                    Signal(a.id, sym, a.headline, _clamp_p(p), impact, reason, iso(a.created_at), self.name,
                           quotes[sym].price)
                )
        return out


# --------------------------------------------------------------------------- claude
SYSTEM_PROMPT = """You are an intraday US-equities news analyst for a paper-trading simulation.
For each news article you receive, decide how it is likely to move each listed ticker it materially concerns,
for the rest of TODAY's regular session.

For every (article, ticker) pair that the article is really about, output:
- p_up: integer 1-99 = probability the ticker's price at today's 4pm ET close will be ABOVE its current price
  (given). 50 = no edge. Be calibrated: routine news, opinion pieces, recaps of moves that already happened, and
  "stocks to watch" lists should stay near 50 (45-55). Reserve <=25 or >=75 for clear, material, NEW information
  (earnings beat/miss with guidance change, M&A, FDA decisions, major contracts, analyst actions from major banks,
  guidance cuts, offerings, fraud/legal shocks).
- Consider whether the move is already priced in: the current intraday change % is provided.
- impact: "low" | "medium" | "high" (how big a price move this news can cause).
- reason: <= 25 words, concrete.
Skip tickers the article only mentions in passing. Output an empty list if nothing is material.

Each ticker comes with the name of the US-listed company it belongs to. News feeds sometimes tag an article
with a US ticker that belongs to a different company (often a foreign company with a similar name or the same
local ticker). If the article is not about that named company, skip the ticker.

Takeovers: once a cash acquisition has been agreed, approved or completed, the target's price is pinned near
the deal price and barely moves, so keep p_up between 45 and 55 for the target. Only a NEW, unexpected
takeover announcement (or a deal collapsing) moves the target's price."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "article_id": {"type": "string"},
                    "symbol": {"type": "string"},
                    "p_up": {"type": "integer"},
                    "impact": {"type": "string", "enum": ["low", "medium", "high"]},
                    "reason": {"type": "string"},
                },
                "required": ["article_id", "symbol", "p_up", "impact", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

# Models that support the server-side refusal fallback parameter
_FALLBACK_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-2", "claude-fable-5-1")


class ClaudeScorer:
    name = "claude"

    def __init__(self, model: str, batch_size: int = 10):
        import anthropic  # imported lazily so the bot runs without the package

        self.anthropic = anthropic
        self.client = anthropic.Anthropic(max_retries=3, timeout=120.0)
        self.model = model
        self.batch_size = batch_size
        self.fallback = LexiconScorer()
        self.input_tokens = 0
        self.output_tokens = 0

    def _payload(self, batch: list[Article], quotes: dict[str, Quote], names: dict[str, str] | None = None) -> str:
        names = names or {}
        rows = []
        for a in batch:
            tickers = []
            for s in a.symbols:
                q = quotes.get(s)
                if not q:
                    continue
                chg = f"{q.change_pct:+.2f}%" if q.change_pct is not None else "n/a"
                t = {"symbol": s, "price": round(q.price, 2), "change_today": chg}
                if names.get(s):
                    t["company"] = names[s]
                tickers.append(t)
            if tickers:
                rows.append({
                    "article_id": a.id,
                    "published_utc": iso(a.created_at),
                    "headline": a.headline,
                    "summary": a.summary[:800],
                    "source": a.source,
                    "tickers": tickers,
                })
        return json.dumps(rows, ensure_ascii=False)

    def _call(self, content: str):
        kwargs = dict(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        )
        if self.model in _FALLBACK_MODELS:
            try:
                return self.client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
                )
            except TypeError:  # older SDK without the fallbacks parameter
                pass
        return self.client.messages.create(**kwargs)

    def score(self, articles: list[Article], quotes: dict[str, Quote],
              names: dict[str, str] | None = None) -> list[Signal]:
        out: list[Signal] = []
        for i in range(0, len(articles), self.batch_size):
            batch = articles[i : i + self.batch_size]
            content = self._payload(batch, quotes, names)
            if content == "[]":
                continue
            try:
                resp = self._call(content)
            except (self.anthropic.RateLimitError, self.anthropic.APIConnectionError, self.anthropic.InternalServerError) as e:
                log.warning("Claude unavailable (%s); using keyword fallback for %d articles", type(e).__name__, len(batch))
                out.extend(self.fallback.score(batch, quotes))
                continue
            except self.anthropic.APIStatusError as e:
                log.error("Claude API error %s: %s; using keyword fallback", e.status_code, e.message)
                out.extend(self.fallback.score(batch, quotes))
                continue
            usage = getattr(resp, "usage", None)
            if usage:
                self.input_tokens += getattr(usage, "input_tokens", 0) or 0
                self.output_tokens += getattr(usage, "output_tokens", 0) or 0
            if resp.stop_reason in ("refusal", "max_tokens"):
                log.warning("Claude stop_reason=%s; keyword fallback for this batch", resp.stop_reason)
                out.extend(self.fallback.score(batch, quotes))
                continue
            text = next((b.text for b in resp.content if b.type == "text"), "")
            try:
                items = json.loads(text).get("items", [])
            except json.JSONDecodeError:
                log.warning("Unparseable Claude output; keyword fallback")
                out.extend(self.fallback.score(batch, quotes))
                continue
            by_id = {a.id: a for a in batch}
            for it in items:
                a = by_id.get(str(it.get("article_id")))
                sym = str(it.get("symbol", "")).upper()
                if not a or sym not in quotes:
                    continue
                impact = it.get("impact") if it.get("impact") in IMPACT_WEIGHT else "low"
                out.append(Signal(a.id, sym, a.headline, _clamp_p(it.get("p_up")), impact,
                                  str(it.get("reason", ""))[:200], iso(a.created_at), f"claude:{self.model}",
                                  quotes[sym].price))
        return out


def make_scorer(settings) -> "ClaudeScorer | LexiconScorer":
    if settings.anthropic_key_present:
        try:
            return ClaudeScorer(settings.claude_model)
        except ImportError:
            log.warning("anthropic package not installed; using keyword scorer")
    return LexiconScorer()
