"""
storage.py — Ma'lumot saqlash qatlami (SQLite)
===============================================
Shamchalarni (OHLCV) saqlaydi va o'qiydi.

Dizayn qarorlari:
  • WAL rejimi — bir vaqtda yozish va o'qish mumkin
  • PRIMARY KEY (symbol, timeframe, timestamp) — dublikat imkonsiz
  • INSERT OR REPLACE — yopilmagan shamcha keyin yangilanadi
  • Timestamp millisekundda (Binance formati)
"""

import sqlite3
from pathlib import Path
from typing import List, Optional, Tuple
from datetime import datetime, timezone


SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    symbol      TEXT    NOT NULL,
    timeframe   TEXT    NOT NULL,
    timestamp   INTEGER NOT NULL,
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    volume      REAL    NOT NULL,
    closed      INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (symbol, timeframe, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_candles_lookup
    ON candles (symbol, timeframe, timestamp DESC);

CREATE TABLE IF NOT EXISTS collection_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT    NOT NULL,
    timeframe   TEXT    NOT NULL,
    event       TEXT    NOT NULL,
    detail      TEXT,
    created_at  TEXT    NOT NULL
);
"""

# Timeframe -> millisekund
TIMEFRAME_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}


def timeframe_to_ms(timeframe: str) -> int:
    if timeframe not in TIMEFRAME_MS:
        raise ValueError(
            f"Noma'lum timeframe: {timeframe}. "
            f"Mavjudlari: {', '.join(TIMEFRAME_MS)}"
        )
    return TIMEFRAME_MS[timeframe]


def ms_to_iso(ms: int) -> str:
    """Millisekundni o'qiladigan sanaga aylantiradi."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M"
    )


class CandleStore:
    """Shamchalar bazasi."""

    def __init__(self, db_path: str = "data/market.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

        # WAL — bir vaqtda o'qish va yozish uchun
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ── Yozish ──────────────────────────────────────────────

    def save_candles(self, symbol: str, timeframe: str,
                     candles: List[list], closed: bool = True) -> int:
        """
        candles: [[timestamp_ms, open, high, low, close, volume], ...]
        Qaytaradi: yozilgan shamchalar soni.
        """
        if not candles:
            return 0

        rows = []
        for c in candles:
            if len(c) < 6:
                continue
            ts, o, h, l, cl, v = c[0], c[1], c[2], c[3], c[4], c[5]

            # Tozalik tekshiruvi — buzuq shamchani yozmaymiz
            if None in (ts, o, h, l, cl, v):
                continue
            if h < l or h < o or h < cl or l > o or l > cl:
                continue
            if o <= 0 or cl <= 0:
                continue

            rows.append((symbol, timeframe, int(ts), float(o), float(h),
                         float(l), float(cl), float(v), 1 if closed else 0))

        if not rows:
            return 0

        self.conn.executemany(
            """INSERT OR REPLACE INTO candles
               (symbol, timeframe, timestamp, open, high, low, close, volume, closed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def log(self, symbol: str, timeframe: str, event: str, detail: str = ""):
        self.conn.execute(
            """INSERT INTO collection_log (symbol, timeframe, event, detail, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (symbol, timeframe, event, detail,
             datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    # ── O'qish ──────────────────────────────────────────────

    def get_candles(self, symbol: str, timeframe: str,
                    since: Optional[int] = None,
                    until: Optional[int] = None,
                    limit: Optional[int] = None,
                    closed_only: bool = True) -> List[dict]:
        """Shamchalarni vaqt bo'yicha o'sish tartibida qaytaradi."""
        query = "SELECT * FROM candles WHERE symbol = ? AND timeframe = ?"
        params: list = [symbol, timeframe]

        if closed_only:
            query += " AND closed = 1"
        if since is not None:
            query += " AND timestamp >= ?"
            params.append(since)
        if until is not None:
            query += " AND timestamp <= ?"
            params.append(until)

        query += " ORDER BY timestamp ASC"
        if limit:
            # Oxirgi N tani olish uchun teskari tartibda olib, qaytaramiz
            query = query.replace("ORDER BY timestamp ASC",
                                  "ORDER BY timestamp DESC")
            query += f" LIMIT {int(limit)}"

        rows = self.conn.execute(query, params).fetchall()
        result = [dict(r) for r in rows]

        if limit:
            result.reverse()
        return result

    def latest_timestamp(self, symbol: str, timeframe: str) -> Optional[int]:
        row = self.conn.execute(
            """SELECT MAX(timestamp) AS ts FROM candles
               WHERE symbol = ? AND timeframe = ? AND closed = 1""",
            (symbol, timeframe),
        ).fetchone()
        return row["ts"] if row and row["ts"] is not None else None

    def earliest_timestamp(self, symbol: str, timeframe: str) -> Optional[int]:
        row = self.conn.execute(
            """SELECT MIN(timestamp) AS ts FROM candles
               WHERE symbol = ? AND timeframe = ?""",
            (symbol, timeframe),
        ).fetchone()
        return row["ts"] if row and row["ts"] is not None else None

    def count(self, symbol: str, timeframe: str, closed_only: bool = True) -> int:
        query = """SELECT COUNT(*) AS n FROM candles
                   WHERE symbol = ? AND timeframe = ?"""
        if closed_only:
            query += " AND closed = 1"
        row = self.conn.execute(query, (symbol, timeframe)).fetchone()
        return row["n"] if row else 0

    # ── Bo'shliqlarni topish ────────────────────────────────

    def find_gaps(self, symbol: str, timeframe: str) -> List[Tuple[int, int]]:
        """
        Ma'lumotdagi teshiklarni topadi.
        Qaytaradi: [(bo'shliq_boshi_ms, bo'shliq_oxiri_ms), ...]

        Bu muhim: backtest teshikli ma'lumotda noto'g'ri natija beradi.
        """
        step = timeframe_to_ms(timeframe)
        rows = self.conn.execute(
            """SELECT timestamp FROM candles
               WHERE symbol = ? AND timeframe = ? AND closed = 1
               ORDER BY timestamp ASC""",
            (symbol, timeframe),
        ).fetchall()

        if len(rows) < 2:
            return []

        gaps = []
        for i in range(1, len(rows)):
            prev_ts = rows[i - 1]["timestamp"]
            curr_ts = rows[i]["timestamp"]
            expected = prev_ts + step
            if curr_ts > expected:
                gaps.append((expected, curr_ts - step))
        return gaps

    def stats(self, symbol: str, timeframe: str) -> dict:
        n = self.count(symbol, timeframe, closed_only=True)
        n_all = self.count(symbol, timeframe, closed_only=False)
        if n == 0:
            return {"count": 0, "xabar": "Ma'lumot yo'q"}

        first = self.earliest_timestamp(symbol, timeframe)
        last = self.latest_timestamp(symbol, timeframe)
        gaps = self.find_gaps(symbol, timeframe)

        step = timeframe_to_ms(timeframe)
        expected = ((last - first) // step) + 1 if last and first else 0
        coverage = (n / expected * 100) if expected else 0

        missing = sum((g[1] - g[0]) // step + 1 for g in gaps)

        return {
            "yopilgan": n,
            "shakllanayotgan": n_all - n,
            "birinchi": ms_to_iso(first) if first else None,
            "oxirgi": ms_to_iso(last) if last else None,
            "kutilgan": expected,
            "qoplash": f"{coverage:.2f}%",
            "bo'shliqlar": len(gaps),
            "yetishmayotgan": missing,
        }

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
