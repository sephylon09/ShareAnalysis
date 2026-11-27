# News Sim Desk

An app that runs on its own during US market hours. It reads US stock news in real time, predicts whether each
stock will **rise or fall** (as a 1-99 % chance), and paper-trades the strongest calls (long or short).
Every buy and sell decision is made live, using only the price at that moment. When the market closes, every call
and trade is checked against the closing price.

**No real money is used. This is a simulation for learning, not investment advice.**

```
 GitHub Actions (free, runs every trading day)            GitHub Pages (free)
 ┌─────────────────────────────────────────────┐         ┌──────────────────────┐
 │ every 60 s:                                  │  push   │ docs/index.html      │
 │  Alpaca news ─► Claude / keyword scorer      │ ──────► │ reads data branch    │
 │  Alpaca prices ─► picks ─► paper entries/exits│ `data`  │ auto-refreshes 60 s  │
 │ after close: grade every call vs close price │ branch  └──────────────────────┘
 └─────────────────────────────────────────────┘
```

## What you get on the dashboard

- **Hot picks**: stocks with a strong news call right now, e.g. `NVDA ▲ RISE 78%`, with the headline and the reasoning.
- **Simulated trades**: for each trade, when it bought (or shorted) and at what price, why, the exit plan set at entry,
  when and why it sold (or covered), the P&L, and a price chart with buy and sell markers.
- **Result at close**: whether the call was right, whether the trade made money, and how our exit compares with
  holding until the close.
- **Prediction log**: every strong call graded right ✔ or wrong ✘ against the closing price, plus overall accuracy.
- **History**: P&L, win rate and accuracy for every day.
- Times are shown in ET and in **your local time**.

## Trading rules (all in `bot/config.py`, each can be changed with an env variable)

| | Rule |
|---|---|
| Fill price | buy = last price **+ $0.01**, sell = last price **− $0.01** (a short is the reverse) |
| Size | $10,000 per trade, at most 5 open trades and 12 trades a day, one trade per stock per day |
| Enter | combined news chance ≥ 70 % RISE (long) or FALL (short); the news is ≤ 45 min old (overnight news counts during the first hour); the price is not moving against the news over the last 5 min; the stock has not already moved > 15 % today; spread ≤ 1 %; price ≥ $5; no entries in the first 5 min or the last 30 min |
| Exit (checked every minute) | stop-loss −1.5 % · **break-even lock**: once a trade is up +0.8 %, it sells if the gain falls back to about zero, so a winner doesn't turn into a loser · trailing stop 0.7 % below the peak once up +1.5 % · take-profit +4 % · new news flips the call · stalled for 2 h with a small gain · always flat 10 min before the close (half days included) |

No rule can promise a trade without a loss. The stop-loss limits the damage, and the break-even lock stops a
trade that was already winning from ending as a loss.

## Setup (about 15 minutes, all free except the optional Claude key)

### 1. Get the API keys

| Key | Needed? | Where | Cost |
|---|---|---|---|
| **Alpaca** `ALPACA_KEY_ID` + `ALPACA_SECRET_KEY` | **required**: live prices, the news feed, the market clock (handles holidays and half days) | sign up at alpaca.markets → *Paper Trading* account → *API Keys* → Generate | free |
| **Anthropic** `ANTHROPIC_API_KEY` | optional but strongly recommended: Claude reads each headline in context | console.anthropic.com → API Keys | pay per use (below) |

Without an Anthropic key the bot uses a free keyword model. It works, but it is much weaker. For example, it
can't tell whether news is already priced in.

**Claude cost estimate**: roughly 500-2,000 articles a day (including overnight news), sent 10 per request.
With the default model `claude-haiku-5-5` this is at most about **$0.12 per trading day**. For deeper analysis,
set the repository variable `CLAUDE_MODEL` to `claude-opus-5-5` (several dollars a day at this volume).
`LLM_MAX_ARTICLES_PER_DAY` (default 2,000) caps the spend. Articles past the cap use the keyword model.

### 2. Put the code on GitHub

1. Create a **public** repository on github.com. GitHub Actions is free with no minute limit for public repos.
   A private repo gets only 2,000 free minutes a month, and the bot needs about 8,000.
2. Upload this folder. You can drag and drop the files on the repo page, or use git:
   ```
   git init && git add . && git commit -m "news sim bot"
   git branch -M main && git remote add origin https://github.com/<you>/<repo>.git && git push -u origin main
   ```
   Make sure `.github/workflows/trader.yml` is uploaded. Folders starting with `.` are easy to miss.

### 3. Add the secrets

Repo → **Settings → Secrets and variables → Actions**:
- *Secrets*: `ALPACA_KEY_ID`, `ALPACA_SECRET_KEY`, and optionally `ANTHROPIC_API_KEY`
- *Variables* (optional): `CLAUDE_MODEL` (default `claude-haiku-5-5`; e.g. `claude-opus-5-5`)

### 4. Turn on the dashboard

Repo → **Settings → Pages** → Source: *Deploy from a branch* → Branch `main`, folder **`/docs`** → Save.
The dashboard is at `https://<you>.github.io/<repo>/`.

### 5. Test it

Repo → **Actions → news-sim-bot → Run workflow**, set `demo` = `true`. This runs a simulated day in about
1 minute and publishes it, so you can check the whole pipeline and dashboard before the market opens.
(The demo day is labelled `-demo`.)

After that it runs by itself every weekday. It wakes about 90 min before the open to analyse overnight news,
trades during the session, and grades everything at the close. Results appear on the dashboard within about
5 minutes, because GitHub caches the data files.

## Optional: desktop copy with moomoo prices

A second copy can run on your Windows PC at the same time as the cloud copy. It uses **moomoo real-time prices**
through moomoo OpenD. News, the market clock and the hot-stock list still come from Alpaca, using the same keys.
It saves to `data-moomoo/` and publishes to the `data-moomoo` branch. On the dashboard, pick
**Desktop · moomoo prices** at the top. The **Cloud vs desktop** table compares the two day by day.

1. **Install and log in to moomoo OpenD.** Download it from moomoo's OpenAPI page, log in with your moomoo account,
   and complete the API questionnaire on first login. Leave it running on the default port `11111`.
   Your moomoo login stays inside OpenD. The bot never sees it.
2. **Install the Python packages** (from the project folder):
   ```powershell
   python -m venv .venv
   .venv\Scripts\pip install -r requirements-desktop.txt
   ```
   `pycryptodome` is pinned to 3.20.0 because Windows Smart App Control blocks the newest build's native file.
3. **Add your keys:** copy `.env.example` to `.env` and fill in the Alpaca and Anthropic keys.
   `.env` is git-ignored.
4. **Try one run by hand:**
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\run-desktop.ps1
   ```
   The log is in `logs\desktop-YYYYMMDD.log`. When the market is closed, it logs "Market closed" and exits.
5. **Make it automatic:**
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\install-desktop-task.ps1
   ```
   This registers the Windows task `NewsSimDesk-moomoo`. It runs on US trading days, every 30 min for 10 h from
   12:00 UTC (20:00 in Singapore), and wakes the PC from sleep. Remove it with
   `Unregister-ScheduledTask -TaskName NewsSimDesk-moomoo`.

Options in `.env`:
- `MOOMOO_PAPER_MIRROR=1` also places each simulated entry and exit as a market order in your moomoo **paper**
  account, so you can watch it in the moomoo app. The code always sends orders to moomoo's SIMULATE environment
  and cannot trade real money. Paper fills follow moomoo's own rules, so they can differ slightly from the
  ±$0.01 simulation.
- `MOOMOO_SECURITY_FIRM` is your moomoo broker entity (`FUTUSG` for Singapore).
- `PUBLISH_DESKTOP=0` keeps desktop results only on your PC.

If OpenD stops responding, the desktop copy uses Alpaca prices for that minute and keeps going.
Your PC must stay on, or at least able to wake from sleep, during US market hours.

## Run it on your own PC instead

```powershell
python -m bot.run --demo --data-dir demo-data            # offline demo, no keys
python -m http.server 8765                                # then open http://localhost:8765/docs/?data=../demo-data/
```
For a live run, set the environment variables and run `python -m bot.run`. Results go to `data/`, which you can
view at `http://localhost:8765/docs/`.

## Files

| Path | What it does |
|---|---|
| `bot/market.py` | Alpaca client: clock, news, real-time IEX snapshots, most-active and movers screener |
| `bot/market_moomoo.py` | moomoo price source and the optional paper-account mirror (desktop copy) |
| `scripts/` | desktop run script and the Windows scheduled-task installer |
| `bot/scorer.py` | Claude scorer (structured JSON output) and the keyword fallback |
| `bot/engine.py` | the loop: picks, entry rules, exit rules, end-of-day grading |
| `bot/sim.py` | fill and P&L math with the ±$0.01 rule |
| `bot/demo.py` | synthetic market for offline testing |
| `docs/index.html` | dashboard (single file, no build step) |
| `.github/workflows/trader.yml` | the scheduler |
| `tests/test_bot.py` | unit tests: `python -m unittest tests.test_bot` |

## Known limits

- Alpaca's free plan prices come from the IEX exchange. They are real time but cover only part of the market's
  volume, so a quiet stock's "last price" can lag by seconds.
- GitHub's scheduler can start a run 5-15 minutes late. The bot catches up on any news it missed, but it can only
  trade at the prices it sees after it starts.
- The workflow fires every 30 min. When a run is already going, the next one waits, and older waiting runs show
  as *cancelled* in the Actions tab. This is expected.
