# ============================================================
#  setup_repo.ps1 — Loyihani professional GitHub repo qilish
#  Ishga tushirish:  .\setup_repo.ps1
# ============================================================

$ErrorActionPreference = "Stop"
Write-Host "`n=== Repo sozlanmoqda ===" -ForegroundColor Cyan

# ── 1. Papka tuzilmasi ──────────────────────────────────────
$dirs = @("core", "analysis", "monitoring", "tests", "data", "logs", "docs", ".github/workflows")
foreach ($d in $dirs) {
    New-Item -ItemType Directory -Path $d -Force | Out-Null
}
Write-Host "  Papkalar tayyor" -ForegroundColor Green

# ── 2. .gitignore ───────────────────────────────────────────
@'
# Muhit
.venv/
venv/
ENV/

# Python
__pycache__/
*.py[cod]
*.egg-info/
.pytest_cache/
.mypy_cache/
.ruff_cache/

# Maxfiy — HECH QACHON commit qilinmaydi
.env
*.key
*.pem
secrets/

# Ma'lumot
*.db
*.db-wal
*.db-shm
*.sqlite3
data/*.csv
data/*.parquet
logs/

# IDE
.vscode/
.idea/
*.swp

# OS
.DS_Store
Thumbs.db
desktop.ini
'@ | Out-File -Encoding utf8 -NoNewline .gitignore

# ── 3. README.md ────────────────────────────────────────────
@'
# Trading Engine

Algorithmic trading infrastructure for crypto futures — data collection,
backtesting, risk management, and live execution.

> **Status:** Under active development. Not financial advice.
> Trading involves substantial risk of loss.

---

## Why this exists

Most retail trading bots fail for two reasons: they are optimised on the
same data they are tested on, and they size positions by feel rather than
by volatility. This project is built to make both mistakes structurally
difficult.

- **Out-of-sample by default** — the backtester refuses to report metrics
  on data the strategy was tuned against.
- **Volatility-adaptive sizing** — position size scales inversely with ATR,
  so risk per trade stays constant in dollar terms.
- **Hard circuit breakers** — daily loss, total drawdown and consecutive-loss
  limits are enforced in the engine, not left to discipline.

---

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Collector   │────▶│   Strategy   │────▶│ Risk Engine  │
│  (Binance)   │     │   (plugin)   │     │  (sizing)    │
└──────┬───────┘     └──────────────┘     └──────┬───────┘
       │                                          │
       ▼                                          ▼
┌──────────────────────────────────┐     ┌──────────────┐
│  SQLite (WAL)                    │◀────│  Executor    │
│  candles · trades · decisions    │     └──────────────┘
└──────┬───────────────────────────┘
       │
       ├──────────────▶ Backtest engine
       ├──────────────▶ Live dashboard
       └──────────────▶ Telegram monitor
```

---

## Modules

| Module | Purpose | Status |
|---|---|---|
| `core/storage.py` | SQLite layer, gap detection, data validation | ✅ |
| `core/collector.py` | Historical backfill, repair, live polling | ✅ |
| `core/risk_engine.py` | ATR-based sizing, circuit breakers, journal | ✅ |
| `analysis/backtest.py` | Event-driven backtester, walk-forward | 🚧 |
| `core/strategy.py` | Strategy plugin interface | 🚧 |
| `core/executor.py` | Order placement and reconciliation | 📋 |
| `monitoring/watchdog.py` | Process health, auto-restart | 📋 |

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

**Why SQLite.** Single writer, many readers — exactly the access pattern
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

MIT — see [LICENSE](LICENSE).

## Disclaimer

This software is provided for educational purposes. Trading cryptocurrency
futures carries a high risk of loss. Past backtest performance does not
indicate future results. The author accepts no liability for financial
losses incurred through use of this software.
'@ | Out-File -Encoding utf8 README.md

# ── 4. LICENSE ──────────────────────────────────────────────
$year = (Get-Date).Year
@"
MIT License

Copyright (c) $year Nurimon Rakhimjonov

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"@ | Out-File -Encoding utf8 LICENSE

# ── 5. GitHub Actions CI ────────────────────────────────────
@'
name: tests

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.11", "3.12"]

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: pip

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Run tests
        run: pytest -v --tb=short
'@ | Out-File -Encoding utf8 .github/workflows/tests.yml

# ── 6. .env.example ─────────────────────────────────────────
@'
# Binance API (demo.binance.com da oling)
BINANCE_API_KEY=
BINANCE_API_SECRET=
BINANCE_TESTNET=true

# Telegram (@BotFather)
TELEGRAM_BOT_TOKEN=
TELEGRAM_USER_ID=

# Savdo sozlamalari
SYMBOL=BTC/USDT
TIMEFRAME=15m
STARTING_BALANCE=500
RISK_PER_TRADE=0.01
'@ | Out-File -Encoding utf8 .env.example

Write-Host "  Hujjatlar tayyor" -ForegroundColor Green

# ── 7. Fayllarni to'g'ri papkalarga ko'chirish ──────────────
$moves = @{
    "storage.py"      = "core\storage.py"
    "collector.py"    = "core\collector.py"
    "risk_engine.py"  = "core\risk_engine.py"
    "test_storage.py" = "tests\test_storage.py"
}
foreach ($src in $moves.Keys) {
    if (Test-Path $src) {
        Move-Item $src $moves[$src] -Force
        Write-Host "  $src -> $($moves[$src])" -ForegroundColor DarkGray
    }
}

# __init__.py fayllari
foreach ($p in @("core", "analysis", "monitoring", "tests")) {
    New-Item -ItemType File -Path "$p\__init__.py" -Force | Out-Null
}

# ── 8. Importlarni to'g'rilash ──────────────────────────────
if (Test-Path "core\collector.py") {
    (Get-Content "core\collector.py" -Raw) `
        -replace 'from storage import', 'from core.storage import' |
        Out-File -Encoding utf8 -NoNewline "core\collector.py"
}
if (Test-Path "tests\test_storage.py") {
    (Get-Content "tests\test_storage.py" -Raw) `
        -replace 'from storage import', 'from core.storage import' |
        Out-File -Encoding utf8 -NoNewline "tests\test_storage.py"
}

# ── 9. pytest sozlamasi ─────────────────────────────────────
@'
[pytest]
testpaths = tests
python_files = test_*.py
addopts = -v --tb=short
'@ | Out-File -Encoding utf8 pytest.ini

# ── 10. requirements.txt ────────────────────────────────────
pip freeze | Out-File -Encoding utf8 requirements.txt
Write-Host "  requirements.txt yangilandi" -ForegroundColor Green

Write-Host "`n=== Tayyor ===" -ForegroundColor Cyan
Write-Host "Keyingi qadam: .\push_repo.ps1`n" -ForegroundColor Yellow
