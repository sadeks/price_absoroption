# price_absorption

Real-time order-flow tooling for CME index futures (ES / MES / NQ / MNQ). The
monitors read the live tape from Interactive Brokers, classify each trade as
aggressive buying or selling, and fire audible alerts when the flow does
something notable. There are also offline scripts for pulling historical bars
and backtesting simple strategies against them.

Everything here is discretionary decision support — the tools read the tape and
alert, they do **not** place orders.

## What's in here

### Live monitors (need a running TWS / IB Gateway)

| Script | Detects | Idea |
| --- | --- | --- |
| `absorption_monitor.py` | **Absorption** — heavy one-sided aggressive flow while price refuses to move. | Passive size is defending a level. Buy absorption (heavy selling, price holds) = support; sell absorption (heavy buying, price holds) = resistance. You supply the location by watching your chart. |
| `absorption.py` | Older absorption implementation, driven by `config.py` / `run.py`. Also logs post-signal MFE/MAE to `absorption_analytics.txt`. | Same concept, uses a max price-change threshold instead of a high-low range check. |
| `momentum.py` | **Momentum** — heavy one-sided flow *with* price moving the same direction. | The opposite of absorption: aggressors are winning, expect continuation. |
| `tickstrike.py` | **Tick-rate spikes** — trades-per-second bursting above baseline, regardless of delta or price move. Classifies the burst bullish / bearish / mixed from the buy/sell volume ratio. | Catches activity surges the delta-based monitors miss. |

All monitors:

- Resolve the front-month contract automatically when `--expiry` is blank.
- Classify trades by comparing print price to the live bid/ask (at-ask = BUY,
  at-bid = SELL, mid = classified by tick direction).
- Compare a short **detection window** against a longer **baseline window** and
  trigger on a multiplier *and* an absolute floor (to suppress low-volume noise).
- Have a per-signal cooldown.
- Play a `.wav` from `sounds/` on a signal, falling back to macOS system
  sounds (`afplay`) if the file is missing.

### Offline scripts

| Script | Purpose |
| --- | --- |
| `historical_data.py` | Downloads 1-second TRADES bars for a date range from IB in 30-min chunks and writes a `*_1sec_RTH_*.csv`. Handles IB's `endDateTime` timezone quirk (see comments in the file). |
| `backtest.py` | Replays your actual executed trades (`Trades_*.csv`) against historical 1-sec bars with a multi-rung ladder / stop / take-profit model. Time-of-day and daily-guardrail filters. |
| `backtest_strategy.py` | Self-contained RSI mean-reversion backtest on resampled 1-sec data, with slippage, ladder, cooldown, and trading-hours filters. |

## Setup

```bash
pip install ib_async pandas numpy
```

Requires Python 3.10+ (uses `X | None` syntax; developed on 3.14).

You need **Interactive Brokers TWS or IB Gateway** running and logged in, with
the API enabled:

- TWS live: port `7496`
- TWS paper: port `7497`
- Gateway: port `4002`

Live tape monitoring requires a CME real-time market-data subscription on the
account.

## Usage

### Live monitors

```bash
# Absorption (recommended entry point), ES front month, defaults
python absorption_monitor.py

python absorption_monitor.py --symbol MES --min-delta 250   # less chatty
python momentum.py --symbol MES --points 3
python tickstrike.py --symbol ES --multiplier 3 --min-ticks 15
```

Common flags across monitors: `--host`, `--port`, `--client-id`, `--symbol`,
`--expiry` (YYYYMM, blank = front month), `--exchange`, `--window`,
`--baseline`. Run any script with `--help` for its full list.

Run each monitor with a **distinct `--client-id`** if you want several at once.

### Config-driven runner

`run.py` reads `config.py` instead of CLI flags:

```bash
python run.py            # absorption (default)
python run.py momentum
```

Edit thresholds, symbol, and connection settings in `config.py`.

### Historical data + backtests

```bash
python historical_data.py 20260701 20260730     # -> mnq_1sec_RTH_20260701_to_20260730.csv
python backtest_strategy.py                       # edit params at top of file
python backtest.py                                # needs a Trades_*.csv export
```

Backtest parameters live as constants at the top of each backtest script.

## Notes

- `sounds/` is empty in the repo — drop in `buy_absorption.wav`,
  `sell_absorption.wav`, etc., or let it fall back to system sounds.
- Alert sounds use `afplay`, so audio is macOS-only. Everything else is
  cross-platform.
- CSV timestamps from `historical_data.py` are in Central Time (exchange time).
