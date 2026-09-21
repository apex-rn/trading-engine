"""
test_backtest.py — Backtest dvigateli testlari
===============================================

Eng muhim testlar: look-ahead bias yo'qligini ISBOTLAYDI.
Agar bu testlar yiqilsa, butun backtest natijasi soxta bo'ladi.
"""

import math
import pytest

from core.strategy import (
    BaseStrategy, Window, Signal, EmaCrossStrategy, BuyAndHoldStrategy
)
from analysis.backtest import (
    Backtest, BacktestConfig, BacktestTrade, split_data, run_split_test
)
from analysis import metrics as M


# ─────────────────────────────────────────────────────────────
# Yordamchilar
# ─────────────────────────────────────────────────────────────

STEP = 900_000  # 15m
T0 = 1_700_000_000_000


def make_candles(prices, step=STEP, t0=T0, spread=0.002):
    """Narxlar ro'yxatidan shamchalar yasaydi."""
    out = []
    for i, p in enumerate(prices):
        prev = prices[i - 1] if i > 0 else p
        out.append({
            "timestamp": t0 + i * step,
            "open": prev,
            "high": max(prev, p) * (1 + spread),
            "low": min(prev, p) * (1 - spread),
            "close": p,
            "volume": 100.0,
        })
    return out


def flat_then_rise(n_flat=300, n_rise=200, base=100.0):
    prices = [base] * n_flat
    for i in range(n_rise):
        prices.append(base * (1 + 0.004 * (i + 1)))
    return prices


# ═════════════════════════════════════════════════════════════
#  1. LOOK-AHEAD HIMOYASI — eng muhim testlar
# ═════════════════════════════════════════════════════════════

class PeekAttemptStrategy(BaseStrategy):
    """Kelajakka qarashga urinadigan strategiya — muvaffaqiyatsiz bo'lishi kerak."""
    name = "peek"
    warmup = 20

    def __init__(self):
        super().__init__()
        self.max_seen_index = -1
        self.window_lengths = []

    def on_candle(self, w: Window) -> None:
        self.window_lengths.append(len(w))
        return None


def test_window_never_exposes_future():
    """Window uzunligi har doim joriy indeksgacha bo'lishi kerak."""
    candles = make_candles([100 + i for i in range(200)])
    strat = PeekAttemptStrategy()
    bt = Backtest(candles, strat, BacktestConfig())
    bt.run()

    # Har chaqiriqda window uzunligi bittaga oshishi kerak
    lengths = strat.window_lengths
    assert len(lengths) > 0
    for i in range(1, len(lengths)):
        assert lengths[i] == lengths[i - 1] + 1, \
            "Window uzunligi noto'g'ri o'sdi — look-ahead xavfi"

    # Oxirgi window butun ma'lumotdan katta bo'lmasligi kerak
    assert max(lengths) <= len(candles)


def test_window_close_matches_current_candle():
    """w.close() joriy shamchaning yopilish narxi bo'lishi kerak."""
    prices = [100 + i * 0.5 for i in range(100)]
    candles = make_candles(prices)

    seen = []

    class Recorder(BaseStrategy):
        name = "rec"
        warmup = 20

        def on_candle(self, w):
            seen.append((w.timestamp, w.close))
            return None

    bt = Backtest(candles, Recorder(), BacktestConfig())
    bt.run()

    for ts, close in seen:
        original = next(c for c in candles if c["timestamp"] == ts)
        assert close == original["close"]


def test_window_series_excludes_future():
    """closes() ro'yxatining oxirgi elementi joriy shamcha bo'lishi kerak."""
    prices = [100 + i for i in range(100)]
    candles = make_candles(prices)

    class Checker(BaseStrategy):
        name = "check"
        warmup = 30

        def on_candle(self, w):
            closes = w.closes()
            assert closes[-1] == w.close
            # Kelajakdagi narx ro'yxatda bo'lmasligi kerak
            idx = next(i for i, c in enumerate(candles)
                       if c["timestamp"] == w.timestamp)
            assert len(closes) == idx + 1
            return None

    bt = Backtest(candles, Checker(), BacktestConfig())
    bt.run()


def test_entry_happens_on_next_candle_open():
    """
    Signal i-shamchada berilsa, pozitsiya (i+1)-shamchaning
    OCHILISH narxida ochilishi kerak. Bu look-ahead'ning asosiy himoyasi.
    """
    prices = [100.0] * 50 + [100.0] * 50
    candles = make_candles(prices, spread=0.001)

    # Aniq bir indeksda signal beradigan strategiya
    signal_at = 60

    class OneShot(BaseStrategy):
        name = "oneshot"
        warmup = 30

        def __init__(self):
            super().__init__()
            self.fired = False

        def on_candle(self, w):
            idx = next(i for i, c in enumerate(candles)
                       if c["timestamp"] == w.timestamp)
            if idx == signal_at and not self.fired:
                self.fired = True
                return Signal(side="long", reason="test")
            return None

    cfg = BacktestConfig(slippage_pct=0.0, taker_fee=0.0)
    bt = Backtest(candles, OneShot(), cfg)
    bt.run()

    assert len(bt.trades) >= 1
    trade = bt.trades[0]

    # Kirish vaqti signal shamchasidan KEYINGI shamcha bo'lishi kerak
    assert trade.entry_time == candles[signal_at + 1]["timestamp"]
    # Kirish narxi o'sha shamchaning ochilish narxi
    assert trade.entry_price == pytest.approx(
        candles[signal_at + 1]["open"], rel=1e-9)


# ═════════════════════════════════════════════════════════════
#  2. IJRO KONSERVATIVLIGI
# ═════════════════════════════════════════════════════════════

def test_stop_loss_wins_when_both_hit():
    """
    Bitta shamchada SL ham, TP ham tegilsa — SL hisoblanishi kerak.
    Bu backtestni haqiqatdan yaxshiroq ko'rsatmaslik uchun.
    """
    cfg = BacktestConfig(slippage_pct=0.0, taker_fee=0.0)
    bt = Backtest(make_candles([100] * 100), BuyAndHoldStrategy(), cfg)

    trade = BacktestTrade(
        entry_time=T0, entry_price=100.0, side="long", size=1.0,
        stop_loss=95.0, take_profit=105.0, risk_amount=5.0, reason="test",
    )

    # Shamcha ikkala darajaga ham tegadi
    candle = {"timestamp": T0, "open": 100, "high": 106,
              "low": 94, "close": 100, "volume": 1}

    result = bt._check_exit(trade, candle)
    assert result is not None
    price, reason = result
    assert reason == "stop_loss"
    assert price == 95.0


def test_short_stop_loss_direction():
    """Short pozitsiyada SL yuqorida, TP pastda bo'lishi kerak."""
    cfg = BacktestConfig(slippage_pct=0.0, taker_fee=0.0)
    bt = Backtest(make_candles([100] * 100), BuyAndHoldStrategy(), cfg)

    trade = BacktestTrade(
        entry_time=T0, entry_price=100.0, side="short", size=1.0,
        stop_loss=105.0, take_profit=95.0, risk_amount=5.0, reason="test",
    )

    # Narx ko'tarildi -> short uchun zarar
    up = {"timestamp": T0, "open": 100, "high": 106,
          "low": 100, "close": 105, "volume": 1}
    price, reason = bt._check_exit(trade, up)
    assert reason == "stop_loss"

    # Narx tushdi -> short uchun foyda
    down = {"timestamp": T0, "open": 100, "high": 100,
            "low": 94, "close": 95, "volume": 1}
    price, reason = bt._check_exit(trade, down)
    assert reason == "take_profit"


def test_fees_reduce_balance():
    """Komissiya balansdan ayirilishi kerak."""
    prices = flat_then_rise()
    candles = make_candles(prices)

    cfg_free = BacktestConfig(taker_fee=0.0, slippage_pct=0.0)
    cfg_paid = BacktestConfig(taker_fee=0.001, slippage_pct=0.0)

    r_free = Backtest(candles, EmaCrossStrategy(20, 50), cfg_free).run()
    r_paid = Backtest(candles, EmaCrossStrategy(20, 50), cfg_paid).run()

    if r_free["metrikalar"]["savdolar"] > 0:
        assert r_paid["jami_komissiya"] > r_free["jami_komissiya"]
        assert (r_paid["metrikalar"]["yakuniy"] <
                r_free["metrikalar"]["yakuniy"])


# ═════════════════════════════════════════════════════════════
#  3. RISK TO'XTATGICHLARI
# ═════════════════════════════════════════════════════════════

class AlwaysLongStrategy(BaseStrategy):
    """Har shamchada long signal beradi — to'xtatgichlarni sinash uchun."""
    name = "always_long"
    warmup = 20

    def on_candle(self, w):
        return Signal(side="long", reason="doim long")


def test_drawdown_breaker_stops_trading():
    """Drawdown chegarasiga yetganda savdo to'xtashi kerak."""
    # Doimiy tushayotgan bozor
    prices = [100 * (0.998 ** i) for i in range(600)]
    candles = make_candles(prices)

    cfg = BacktestConfig(
        starting_balance=500,
        max_total_drawdown=0.10,
        safety_buffer=0.8,
        max_trades_per_day=999,
        max_consecutive_losses=999,
        max_daily_loss=0.99,
        mode="prop",
    )
    bt = Backtest(candles, AlwaysLongStrategy(), cfg)
    res = bt.run()

    # Balans boshlang'ichning 90% idan pastga tushmasligi kerak
    assert bt.balance > cfg.starting_balance * 0.85
    assert "umumiy_drawdown" in res["rad_etilgan"]
    assert res["prop_buzilish"] is not None


def test_research_mode_continues_after_breach():
    """
    Research rejimida drawdown buzilishi qayd etiladi, lekin savdo
    davom etadi — strategiyani butun davrda ko'rish uchun.
    """
    prices = [100 * (0.998 ** i) for i in range(600)]
    candles = make_candles(prices)

    common = dict(starting_balance=500, max_total_drawdown=0.10,
                  max_trades_per_day=999, max_consecutive_losses=999,
                  max_daily_loss=0.99)

    prop = Backtest(candles, AlwaysLongStrategy(),
                    BacktestConfig(mode="prop", **common)).run()
    research = Backtest(candles, AlwaysLongStrategy(),
                        BacktestConfig(mode="research", **common)).run()

    # Ikkalasida ham buzilish qayd etilgan
    assert prop["prop_buzilish"] is not None
    assert research["prop_buzilish"] is not None

    # Research rejimida ko'proq savdo bo'ladi (to'xtamaydi)
    assert (research["metrikalar"]["savdolar"] >
            prop["metrikalar"]["savdolar"])
    assert "umumiy_drawdown" not in research["rad_etilgan"]


def test_breach_records_first_occurrence_only():
    """Buzilish faqat birinchi marta yoziladi."""
    prices = [100 * (0.997 ** i) for i in range(800)]
    candles = make_candles(prices)

    bt = Backtest(candles, AlwaysLongStrategy(),
                  BacktestConfig(mode="research", max_trades_per_day=999,
                                 max_consecutive_losses=999,
                                 max_daily_loss=0.99))
    bt.run()

    assert bt.prop_breach is not None
    first_time = bt.prop_breach["time"]
    # Birinchi buzilish vaqti ma'lumotning oxiridan oldin bo'lishi kerak
    assert first_time < candles[-1]["timestamp"]


def test_market_benchmark_reported():
    """Natijada bozor foizi bo'lishi kerak."""
    prices = [100 + i * 0.1 for i in range(500)]
    candles = make_candles(prices)
    res = Backtest(candles, EmaCrossStrategy(10, 30), BacktestConfig()).run()

    expected = (candles[-1]["close"] - candles[0]["close"]) / \
        candles[0]["close"] * 100
    assert res["bozor_%"] == pytest.approx(expected, rel=1e-3)


def test_consecutive_loss_breaker():
    """Ketma-ket zararlar chegarasi ishlashi kerak."""
    prices = [100 * (0.995 ** i) for i in range(400)]
    candles = make_candles(prices)

    cfg = BacktestConfig(
        max_consecutive_losses=2,
        max_trades_per_day=999,
        max_total_drawdown=0.99,
    )
    bt = Backtest(candles, AlwaysLongStrategy(), cfg)
    res = bt.run()

    assert "ketma_ket_zararlar" in res["rad_etilgan"]


def test_daily_trade_limit():
    """Kunlik savdo soni cheklanishi kerak."""
    # Bir kunga 96 ta 15m shamcha sig'adi
    prices = [100 + (i % 10) for i in range(500)]
    candles = make_candles(prices)

    cfg = BacktestConfig(
        max_trades_per_day=2,
        max_consecutive_losses=999,
        max_total_drawdown=0.99,
        max_bars_in_trade=1,      # har savdo 1 shamchada yopiladi
    )
    bt = Backtest(candles, AlwaysLongStrategy(), cfg)
    res = bt.run()

    assert "kunlik_savdo_limiti" in res["rad_etilgan"]

    # Har kunda ko'pi bilan 2 ta savdo bo'lishi kerak
    from collections import Counter
    per_day = Counter(t.entry_time // 86_400_000 for t in bt.trades)
    assert all(n <= 2 for n in per_day.values())


def test_one_position_at_a_time():
    """Bir vaqtda faqat bitta pozitsiya ochiq bo'lishi kerak."""
    prices = [100 + math.sin(i / 10) * 5 for i in range(500)]
    candles = make_candles(prices)

    cfg = BacktestConfig(one_position_at_a_time=True,
                         max_trades_per_day=999,
                         max_consecutive_losses=999)
    bt = Backtest(candles, AlwaysLongStrategy(), cfg)
    bt.run()

    # Savdolar vaqt bo'yicha kesishmasligi kerak
    for i in range(1, len(bt.trades)):
        prev_exit = bt.trades[i - 1].exit_time
        curr_entry = bt.trades[i].entry_time
        assert curr_entry >= prev_exit, "Pozitsiyalar kesishdi"


# ═════════════════════════════════════════════════════════════
#  4. TRAIN / TEST BO'LINISHI
# ═════════════════════════════════════════════════════════════

def test_split_preserves_time_order():
    """Bo'linish vaqt tartibini buzmasligi kerak."""
    candles = make_candles([100 + i for i in range(1000)])
    train, test = split_data(candles, 0.7)

    assert len(train) == 700
    assert len(test) == 300
    # Train'ning oxirgisi test'ning birinchisidan oldin
    assert train[-1]["timestamp"] < test[0]["timestamp"]


def test_split_no_overlap():
    """Train va test kesishmasligi kerak."""
    candles = make_candles([100 + i for i in range(1000)])
    train, test = split_data(candles, 0.7)

    train_ts = {c["timestamp"] for c in train}
    test_ts = {c["timestamp"] for c in test}
    assert train_ts.isdisjoint(test_ts)


def test_split_rejects_bad_ratio():
    candles = make_candles([100] * 100)
    with pytest.raises(ValueError):
        split_data(candles, 0.05)
    with pytest.raises(ValueError):
        split_data(candles, 0.99)


def test_strategy_state_not_shared_between_splits():
    """
    run_split_test har safar yangi strategiya yaratishi kerak.
    Aks holda train'dagi holat test'ga sizib o'tadi.
    """
    candles = make_candles([100 + i * 0.1 for i in range(2000)])

    created = []

    def factory():
        s = BuyAndHoldStrategy()
        created.append(s)
        return s

    run_split_test(candles, factory, BacktestConfig())

    assert len(created) == 2
    assert created[0] is not created[1]


# ═════════════════════════════════════════════════════════════
#  5. METRIKALAR
# ═════════════════════════════════════════════════════════════

def test_max_drawdown_basic():
    equity = [100, 120, 90, 110, 80, 130]
    dd = M.max_drawdown(equity)
    # Cho'qqi 120, tub 80 -> (120-80)/120 = 33.33%
    assert dd["max_dd_pct"] == pytest.approx(0.3333, rel=1e-3)


def test_max_drawdown_monotonic_rise():
    equity = [100, 110, 120, 130]
    dd = M.max_drawdown(equity)
    assert dd["max_dd_pct"] == 0.0


def test_profit_factor():
    assert M.profit_factor([10, -5, 10, -5]) == pytest.approx(2.0)
    assert M.profit_factor([-10, -10]) == 0.0
    assert M.profit_factor([10, 10]) == float("inf")


def test_win_rate():
    assert M.win_rate([1, 1, -1, -1]) == 0.5
    assert M.win_rate([]) == 0.0
    assert M.win_rate([1, 1, 1]) == 1.0


def test_longest_losing_streak():
    assert M.longest_losing_streak([1, -1, -1, -1, 1, -1]) == 3
    assert M.longest_losing_streak([1, 1, 1]) == 0
    assert M.longest_losing_streak([-1, -1]) == 2


def test_expectancy_r():
    assert M.expectancy_r([2.0, -1.0, 2.0, -1.0]) == pytest.approx(0.5)


def test_sharpe_requires_variance():
    # Bir xil daromad -> std=0 -> None
    assert M.sharpe_ratio([0.01] * 10, periods_per_year=100) is None
    assert M.sharpe_ratio([0.01], periods_per_year=100) is None


def test_sharpe_annualization_is_sane():
    """
    Sharpe real diapazonda bo'lishi kerak. Oldingi xato:
    savdo daromadlari 15m shamchalar soni bilan yillikka
    keltirilgan va -125 kabi absurd qiymatlar chiqqan.
    """
    # Yiliga 50 savdo, o'rtacha +0.5%, std 2%
    import random
    random.seed(1)
    returns = [random.gauss(0.005, 0.02) for _ in range(50)]
    s = M.sharpe_ratio(returns, periods_per_year=50)
    assert s is not None
    assert -10 < s < 10, f"Sharpe absurd: {s}"


def test_compute_all_uses_duration():
    """compute_all davr uzunligidan yiliga savdolar sonini hisoblaydi."""
    trades = [{"pnl": p, "r_multiple": p / 5}
              for p in [10, -5, 8, -5, 12, -5, 6, -5, 9, -5]]
    equity = [500.0]
    for t in trades:
        equity.append(equity[-1] + t["pnl"])

    one_year_ms = int(365.25 * 86_400_000)
    m = M.compute_all(trades, equity, 500.0, duration_ms=one_year_ms)
    assert m["sharpe"] is not None
    assert -10 < m["sharpe"] < 10


def test_metrics_empty_trades():
    m = M.compute_all([], [500.0], 500.0)
    assert m["savdolar"] == 0
    assert "xabar" in m


# ═════════════════════════════════════════════════════════════
#  6. UMUMIY MUSTAHKAMLIK
# ═════════════════════════════════════════════════════════════

def test_rejects_too_few_candles():
    with pytest.raises(ValueError):
        Backtest(make_candles([100] * 10), BuyAndHoldStrategy())


def test_unsorted_candles_get_sorted():
    candles = make_candles([100 + i for i in range(300)])
    shuffled = candles[150:] + candles[:150]

    bt = Backtest(shuffled, BuyAndHoldStrategy(), BacktestConfig())
    for i in range(1, len(bt.candles)):
        assert bt.candles[i]["timestamp"] > bt.candles[i - 1]["timestamp"]


def test_strategy_exception_does_not_crash():
    """Strategiyadagi xato butun backtestni yiqitmasligi kerak."""

    class BrokenStrategy(BaseStrategy):
        name = "broken"
        warmup = 20

        def on_candle(self, w):
            raise RuntimeError("ataylab xato")

    candles = make_candles([100 + i for i in range(300)])
    bt = Backtest(candles, BrokenStrategy(), BacktestConfig())
    res = bt.run()

    assert res["metrikalar"]["savdolar"] == 0
    assert any("strategiya_xatosi" in k for k in res["rad_etilgan"])


def test_open_position_closed_at_end():
    """Backtest oxirida ochiq pozitsiya yopilishi kerak."""
    candles = make_candles([100 + i * 0.01 for i in range(500)])
    bt = Backtest(candles, BuyAndHoldStrategy(), BacktestConfig())
    bt.run()

    assert bt.open_trade is None
    if bt.trades:
        assert all(t.exit_price is not None for t in bt.trades)


def test_equity_curve_matches_trades():
    """Equity curve savdolar soniga mos kelishi kerak."""
    prices = [100 + math.sin(i / 20) * 10 for i in range(1000)]
    candles = make_candles(prices)

    bt = Backtest(candles, EmaCrossStrategy(10, 30),
                  BacktestConfig(max_trades_per_day=999,
                                 max_consecutive_losses=999))
    bt.run()

    # Boshlang'ich + har savdo uchun bitta nuqta
    assert len(bt.equity_curve) == len(bt.trades) + 1


def test_r_multiple_calculation():
    """R multiple = pnl / risk_amount bo'lishi kerak."""
    prices = flat_then_rise()
    candles = make_candles(prices)

    bt = Backtest(candles, EmaCrossStrategy(20, 50), BacktestConfig())
    bt.run()

    for t in bt.trades:
        if t.risk_amount > 0:
            assert t.r_multiple == pytest.approx(t.pnl / t.risk_amount,
                                                 rel=1e-6)


# ═════════════════════════════════════════════════════════════
#  7. INDIKATOR KESHI — to'g'rilik va tezlik
# ═════════════════════════════════════════════════════════════

def _naive_ema(closes, period):
    """Eski, keshsiz EMA — solishtirish uchun o'lchov."""
    if len(closes) < period:
        return None
    k = 2 / (period + 1)
    e = sum(closes[:period]) / period
    for v in closes[period:]:
        e = v * k + e * (1 - k)
    return e


def _naive_rsi(closes, period):
    """Eski, keshsiz Wilder RSI."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for j in range(1, len(closes)):
        ch = closes[j] - closes[j - 1]
        gains.append(max(ch, 0))
        losses.append(max(-ch, 0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for j in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[j]) / period
        al = (al * (period - 1) + losses[j]) / period
    if al == 0:
        return 100.0
    return 100 - (100 / (1 + ag / al))


def test_cached_ema_matches_naive():
    """Keshlangan EMA eski hisoblash bilan aynan bir xil bo'lishi kerak."""
    import random
    random.seed(3)
    prices = [100.0]
    for _ in range(600):
        prices.append(prices[-1] * (1 + random.gauss(0, 0.01)))
    candles = make_candles(prices)
    cache = {}

    for i in range(0, len(candles), 37):
        w = Window(candles, i, cache)
        closes = [c["close"] for c in candles[:i + 1]]
        for period in (5, 20, 50):
            for offset in (0, 1):
                got = w.ema(period, offset=offset)
                exp = _naive_ema(closes[:len(closes) - offset]
                                 if offset else closes, period)
                if exp is None:
                    assert got is None
                else:
                    assert got == pytest.approx(exp, rel=1e-12)


def test_cached_rsi_matches_naive():
    import random
    random.seed(4)
    prices = [100.0]
    for _ in range(500):
        prices.append(prices[-1] * (1 + random.gauss(0, 0.01)))
    candles = make_candles(prices)
    cache = {}

    for i in range(0, len(candles), 29):
        w = Window(candles, i, cache)
        closes = [c["close"] for c in candles[:i + 1]]
        got = w.rsi(14)
        exp = _naive_rsi(closes, 14)
        if exp is None:
            assert got is None
        else:
            assert got == pytest.approx(exp, rel=1e-12)


def test_cache_does_not_leak_future():
    """
    Kesh butun qatorni saqlaydi, lekin Window faqat joriy indeksgacha
    o'qishi kerak. Kelajakdagi narxni o'zgartirsak, o'tmishdagi EMA
    o'zgarmasligi kerak.
    """
    prices = [100 + i * 0.3 for i in range(300)]
    a = make_candles(prices)
    b = make_candles(prices[:200] + [999.0] * 100)   # kelajak boshqacha

    wa = Window(a, 150, {})
    wb = Window(b, 150, {})
    assert wa.ema(20) == pytest.approx(wb.ema(20), rel=1e-12)
    assert wa.rsi(14) == pytest.approx(wb.rsi(14), rel=1e-12)


def test_backtest_performance_prop_mode():
    """
    Regressiya testi: prop rejimida drawdown buzilgach strategiya
    har shamchada chaqiriladi. Oldingi O(n^2) EMA bu yerda qotib qolardi.
    20,000 shamcha bir necha soniyada tugashi kerak.
    """
    import time
    prices = [100 * (0.9999 ** i) for i in range(20_000)]
    candles = make_candles(prices)

    cfg = BacktestConfig(mode="prop", max_trades_per_day=999,
                         max_consecutive_losses=999, max_daily_loss=0.99)
    t0 = time.perf_counter()
    Backtest(candles, EmaCrossStrategy(50, 200), cfg).run()
    elapsed = time.perf_counter() - t0

    assert elapsed < 10.0, f"Backtest juda sekin: {elapsed:.1f}s"
