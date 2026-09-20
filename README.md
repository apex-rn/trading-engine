# Trading Engine

Algorithmic trading infrastructure for crypto futures â€” data collection,
backtesting, risk management, and live execution.

> **Status:** Under active development. Not financial advice.
> Trading involves substantial risk of loss.

---

## Why this exists

Most retail trading bots fail for two reasons: they are optimised on the
same data they are tested on, and they size positions by feel rather than
by volatility. This project is built to make both mistakes structurally
difficult.

- **Out-of-sample by default** â€” the backtester refuses to report metrics
  on data the strategy was tuned against.
- **Volatility-adaptive sizing** â€” position size scales inversely with ATR,
  so risk per trade stays constant in dollar terms.
- **Hard circuit breakers** â€” daily loss, total drawdown and consecutive-loss
  limits are enforced in the engine, not left to discipline.

---

## Architecture

```
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”     â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”     â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚  Collector   â”‚â”€â”€â”€â”€â–¶â”‚   Strategy   â”‚â”€â”€â”€â”€â–¶â”‚ Risk Engine  â”‚
â”‚  (Binance)   â”‚     â”‚   (plugin)   â”‚     â”‚  (sizing)    â”‚
â””â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”˜     â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜     â””â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”˜
       â”‚                                          â”‚
       â–¼                                          â–¼
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”     â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚  SQLite (WAL)                    â”‚â—€â”€â”€â”€â”€â”‚  Executor    â”‚
â”‚  candles Â· trades Â· decisions    â”‚     â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
â””â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
       â”‚
       â”œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â–¶ Backtest engine
       â”œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â–¶ Live dashboard
       â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â–¶ Telegram monitor
```

---

## Modules

| Module | Purpose | Status |
|---|---|---|
| `core/storage.py` | SQLite layer, gap detection, data validation | âœ… |
| `core/collector.py` | Historical backfill, repair, live polling | âœ… |
| `core/risk_engine.py` | ATR-based sizing, circuit breakers, journal | âœ… |
| `analysis/backtest.py` | Event-driven backtester, walk-forward | ðŸš§ |
| `core/strategy.py` | Strategy plugin interface | ðŸš§ |
| `core/executor.py` | Order placement and reconciliation | ðŸ“‹ |
| `monitoring/watchdog.py` | Process health, auto-restart | ðŸ“‹ |

---

## Quick start

```bash
git clone https://github.com/USERNAME/trading-engine.git
cd trading-engine

python -m venv .venv
source .venv/bin/activate        # Windows: .\.venv\Scripts\Activate.ps1

pip install -r requirements.txt
cp .env.example .env             # then fill in your keys
```

### Collect data

```bash
python -m core.collector backfill --days 365   # historical
python -m core.collector repair                # fill gaps
python -m core.collector stats                 # verify coverage
python -m core.collector live                  # stream new candles
```

Coverage should read `100.00%` before you backtest anything.

---

## Design notes

**Why polling instead of WebSocket.** The engine trades on closed candles
at 15m and above. A WebSocket stream adds reconnection complexity for
latency that never matters at that timeframe. One REST call per candle
close is well inside rate limits.

**Why SQLite.** Single writer, many readers â€” exactly the access pattern
here. WAL mode lets the dashboard read while the collector writes. No
server to run or secure on the VPS.

**Why `INSERT OR REPLACE`.** The current candle is unclosed and mutates
until its period ends. A composite primary key on
`(symbol, timeframe, timestamp)` makes duplicates impossible and lets the
forming candle update in place.

---

## Testing

```bash
pytest -v
```

The storage layer is tested for deduplication, gap detection, malformed
candle rejection, and forming-candle updates.

---

## License

MIT â€” see [LICENSE](LICENSE).

## Disclaimer

This software is provided for educational purposes. Trading cryptocurrency
futures carries a high risk of loss. Past backtest performance does not
indicate future results. The author accepts no liability for financial
losses incurred through use of this software.
