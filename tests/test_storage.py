"""
test_storage.py â€” Ma'lumot qatlami testlari
============================================
Ishga tushirish:
    pytest test_storage.py -v
"""

import os
import tempfile
import pytest

from core.storage import CandleStore, timeframe_to_ms, ms_to_iso


@pytest.fixture
def store():
    """Har test uchun vaqtinchalik baza."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = CandleStore(path)
    yield s
    s.close()
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass


def make_candles(start_ms: int, count: int, step: int, price: float = 100.0):
    """Test uchun to'g'ri shamchalar yasaydi."""
    return [
        [start_ms + i * step, price, price + 2, price - 2, price + 1, 1000.0]
        for i in range(count)
    ]


# â”€â”€ Asosiy yozish/o'qish â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_save_and_read(store):
    step = timeframe_to_ms("15m")
    candles = make_candles(1_700_000_000_000, 10, step)

    n = store.save_candles("BTC/USDT", "15m", candles)
    assert n == 10

    rows = store.get_candles("BTC/USDT", "15m")
    assert len(rows) == 10
    assert rows[0]["timestamp"] < rows[-1]["timestamp"]  # o'sish tartibida


def test_no_duplicates(store):
    """Bir xil shamchani ikki marta yozsak, bitta qoladi."""
    step = timeframe_to_ms("15m")
    candles = make_candles(1_700_000_000_000, 5, step)

    store.save_candles("BTC/USDT", "15m", candles)
    store.save_candles("BTC/USDT", "15m", candles)

    assert store.count("BTC/USDT", "15m") == 5


def test_symbols_isolated(store):
    """Turli symbol va timeframe aralashmaydi."""
    step = timeframe_to_ms("15m")
    candles = make_candles(1_700_000_000_000, 5, step)

    store.save_candles("BTC/USDT", "15m", candles)
    store.save_candles("ETH/USDT", "15m", candles)
    store.save_candles("BTC/USDT", "1h", candles)

    assert store.count("BTC/USDT", "15m") == 5
    assert store.count("ETH/USDT", "15m") == 5
    assert store.count("BTC/USDT", "1h") == 5


# â”€â”€ Tozalik tekshiruvi â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_rejects_invalid_high_low(store):
    """high < low bo'lgan shamcha rad etiladi."""
    bad = [[1_700_000_000_000, 100, 90, 110, 105, 1000]]  # high=90 < low=110
    n = store.save_candles("BTC/USDT", "15m", bad)
    assert n == 0


def test_rejects_none_values(store):
    bad = [[1_700_000_000_000, 100, None, 95, 99, 1000]]
    n = store.save_candles("BTC/USDT", "15m", bad)
    assert n == 0


def test_rejects_zero_price(store):
    bad = [[1_700_000_000_000, 0, 10, 0, 0, 1000]]
    n = store.save_candles("BTC/USDT", "15m", bad)
    assert n == 0


# â”€â”€ Bo'shliqlar â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_no_gaps_when_continuous(store):
    step = timeframe_to_ms("15m")
    candles = make_candles(1_700_000_000_000, 20, step)
    store.save_candles("BTC/USDT", "15m", candles)

    assert store.find_gaps("BTC/USDT", "15m") == []


def test_detects_single_gap(store):
    step = timeframe_to_ms("15m")
    start = 1_700_000_000_000

    first = make_candles(start, 5, step)
    # 3 ta shamcha tashlab ketamiz
    second = make_candles(start + 8 * step, 5, step)

    store.save_candles("BTC/USDT", "15m", first)
    store.save_candles("BTC/USDT", "15m", second)

    gaps = store.find_gaps("BTC/USDT", "15m")
    assert len(gaps) == 1
    gap_start, gap_end = gaps[0]
    assert gap_start == start + 5 * step
    assert gap_end == start + 7 * step


def test_detects_multiple_gaps(store):
    step = timeframe_to_ms("15m")
    start = 1_700_000_000_000

    store.save_candles("BTC/USDT", "15m", make_candles(start, 3, step))
    store.save_candles("BTC/USDT", "15m", make_candles(start + 6 * step, 3, step))
    store.save_candles("BTC/USDT", "15m", make_candles(start + 12 * step, 3, step))

    assert len(store.find_gaps("BTC/USDT", "15m")) == 2


# â”€â”€ Timestamp yordamchilari â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_latest_and_earliest(store):
    step = timeframe_to_ms("15m")
    start = 1_700_000_000_000
    store.save_candles("BTC/USDT", "15m", make_candles(start, 10, step))

    assert store.earliest_timestamp("BTC/USDT", "15m") == start
    assert store.latest_timestamp("BTC/USDT", "15m") == start + 9 * step


def test_empty_store_returns_none(store):
    assert store.latest_timestamp("BTC/USDT", "15m") is None
    assert store.count("BTC/USDT", "15m") == 0


def test_limit_returns_most_recent(store):
    step = timeframe_to_ms("15m")
    start = 1_700_000_000_000
    store.save_candles("BTC/USDT", "15m", make_candles(start, 100, step))

    rows = store.get_candles("BTC/USDT", "15m", limit=10)
    assert len(rows) == 10
    assert rows[-1]["timestamp"] == start + 99 * step   # eng yangisi oxirida
    assert rows[0]["timestamp"] == start + 90 * step


# â”€â”€ Yopilmagan shamchalar â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_closed_only_filter(store):
    step = timeframe_to_ms("15m")
    start = 1_700_000_000_000

    store.save_candles("BTC/USDT", "15m", make_candles(start, 5, step), closed=True)
    store.save_candles("BTC/USDT", "15m",
                       make_candles(start + 5 * step, 1, step), closed=False)

    assert len(store.get_candles("BTC/USDT", "15m", closed_only=True)) == 5
    assert len(store.get_candles("BTC/USDT", "15m", closed_only=False)) == 6
    assert store.count("BTC/USDT", "15m", closed_only=False) == 6


def test_forming_candle_updates(store):
    """Shakllanayotgan shamcha yangilanadi, dublikat yaratmaydi."""
    ts = 1_700_000_000_000

    store.save_candles("BTC/USDT", "15m", [[ts, 100, 101, 99, 100, 10]], closed=False)
    store.save_candles("BTC/USDT", "15m", [[ts, 100, 105, 99, 104, 50]], closed=False)

    rows = store.get_candles("BTC/USDT", "15m", closed_only=False)
    assert len(rows) == 1
    assert rows[0]["close"] == 104
    assert rows[0]["volume"] == 50


# â”€â”€ Timeframe â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_timeframe_conversion():
    assert timeframe_to_ms("1m") == 60_000
    assert timeframe_to_ms("15m") == 900_000
    assert timeframe_to_ms("1h") == 3_600_000
    assert timeframe_to_ms("1d") == 86_400_000


def test_unknown_timeframe_raises():
    with pytest.raises(ValueError):
        timeframe_to_ms("7m")


# â”€â”€ Statistika â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_stats_full_coverage(store):
    step = timeframe_to_ms("15m")
    store.save_candles("BTC/USDT", "15m",
                       make_candles(1_700_000_000_000, 50, step))

    s = store.stats("BTC/USDT", "15m")
    assert s["yopilgan"] == 50
    assert s["bo'shliqlar"] == 0
    assert s["qoplash"] == "100.00%"


def test_stats_with_gap(store):
    step = timeframe_to_ms("15m")
    start = 1_700_000_000_000

    store.save_candles("BTC/USDT", "15m", make_candles(start, 5, step))
    store.save_candles("BTC/USDT", "15m", make_candles(start + 10 * step, 5, step))

    s = store.stats("BTC/USDT", "15m")
    assert s["yopilgan"] == 10
    assert s["bo'shliqlar"] == 1
    assert s["yetishmayotgan"] == 5
