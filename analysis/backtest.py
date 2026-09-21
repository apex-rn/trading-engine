"""
backtest.py — Backtest dvigateli
=================================

Dizayn prinsipi: BACKTEST YOLG'ON GAPIRMASLIGI KERAK.

Shuning uchun quyidagilar qattiq ta'minlanadi:

1. LOOK-AHEAD YO'Q
   Signal i-shamchaning YOPILISHIDA hisoblanadi, lekin pozitsiya
   (i+1)-shamchaning OCHILISHIDA ochiladi. Real hayotda ham shunday:
   shamcha yopilganini ko'rib, keyingisida kirasiz.

2. IJRO KONSERVATIV
   Bitta shamchada ham SL, ham TP tegilsa — SL hisoblanadi.
   Chunki qaysi biri oldin bo'lganini bilmaymiz, va yomon holatni
   tanlash xavfsizroq.

3. XARAJATLAR HISOBGA OLINADI
   Komissiya (taker) va slippage har kirish-chiqishda ayiriladi.

4. TRAIN/TEST AJRATILGAN
   Strategiya sozlangan ma'lumotda o'lchov olinmaydi.
"""

from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any
import math

from core.strategy import BaseStrategy, Window, Signal
from analysis import metrics as M


# ─────────────────────────────────────────────────────────────
# Sozlamalar
# ─────────────────────────────────────────────────────────────

@dataclass
class BacktestConfig:
    starting_balance: float = 500.0

    # Risk
    risk_per_trade: float = 0.01
    max_risk_per_trade: float = 0.02
    atr_period: int = 14
    atr_multiplier: float = 2.0
    vol_scaling: bool = True

    # To'xtatgichlar
    max_daily_loss: float = 0.05
    max_total_drawdown: float = 0.10
    safety_buffer: float = 0.8
    max_consecutive_losses: int = 3
    max_trades_per_day: int = 5

    # Xarajatlar (Binance futures taker = 0.04%)
    taker_fee: float = 0.0004
    slippage_pct: float = 0.0002      # 0.02% — konservativ taxmin

    # Pozitsiya
    one_position_at_a_time: bool = True
    max_bars_in_trade: Optional[int] = None   # majburiy yopish

    timeframe: str = "15m"

    # Rejim:
    #   "research" — drawdown chegarasi qayd etiladi, lekin savdo davom
    #                etadi. Strategiyani butun davrda ko'rish uchun.
    #   "prop"     — chegara buzilsa savdo butunlay to'xtaydi (prop
    #                firma challenge'ini taqlid qiladi).
    mode: str = "research"


# ─────────────────────────────────────────────────────────────
# Savdo yozuvi
# ─────────────────────────────────────────────────────────────

@dataclass
class BacktestTrade:
    entry_time: int
    entry_price: float
    side: str
    size: float
    stop_loss: float
    take_profit: float
    risk_amount: float
    reason: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    exit_time: Optional[int] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    r_multiple: Optional[float] = None
    fees: float = 0.0
    bars_held: int = 0


# ─────────────────────────────────────────────────────────────
# Dvigatel
# ─────────────────────────────────────────────────────────────

class Backtest:

    def __init__(self, candles: List[dict], strategy: BaseStrategy,
                 config: Optional[BacktestConfig] = None):
        if len(candles) < 50:
            raise ValueError(f"Juda kam shamcha: {len(candles)}")

        # Vaqt bo'yicha tartiblanganini kafolatlaymiz
        self.candles = sorted(candles, key=lambda c: c["timestamp"])
        self.strategy = strategy
        self.cfg = config or BacktestConfig()

        self.balance = self.cfg.starting_balance
        self.peak_balance = self.balance
        self.equity_curve: List[float] = [self.balance]

        self.trades: List[BacktestTrade] = []
        self.open_trade: Optional[BacktestTrade] = None
        self.rejected: List[dict] = []     # qaror jurnali

        self._consecutive_losses = 0
        self._day_start_balance = self.balance
        self._current_day: Optional[int] = None
        self._trades_today = 0

        # Prop qoidasi birinchi marta qachon buzilgani
        self.prop_breach: Optional[dict] = None

        # Indikator keshi — barcha Window'lar ulashadi
        self._indicator_cache: dict = {}

    # ── Yordamchilar ────────────────────────────────────────

    @staticmethod
    def _day_of(ts_ms: int) -> int:
        return ts_ms // 86_400_000

    def _atr(self, i: int) -> Optional[float]:
        """i-shamchadagi ATR — faqat o'tgan ma'lumotdan."""
        p = self.cfg.atr_period
        if i < p:
            return None
        trs = []
        for j in range(i - p + 1, i + 1):
            h = self.candles[j]["high"]
            l = self.candles[j]["low"]
            pc = self.candles[j - 1]["close"]
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        return sum(trs) / p

    def _avg_atr(self, i: int, lookback: int = 100) -> Optional[float]:
        """Uzoq muddatli o'rtacha ATR — volatillikni solishtirish uchun."""
        p = self.cfg.atr_period
        start = max(p, i - lookback)
        if i <= start:
            return None
        vals = [self._atr(j) for j in range(start, i + 1)]
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None

    # ── Risk tekshiruvlari ──────────────────────────────────

    def _can_trade(self, ts: int) -> tuple[bool, str]:
        c = self.cfg

        day = self._day_of(ts)
        if day != self._current_day:
            self._current_day = day
            self._day_start_balance = self.balance
            self._trades_today = 0
            self._consecutive_losses = 0

        daily_pnl = self.balance - self._day_start_balance
        daily_limit = self._day_start_balance * c.max_daily_loss * c.safety_buffer
        if daily_pnl <= -daily_limit:
            if self.prop_breach is None:
                self.prop_breach = {
                    "time": ts,
                    "rule": "kunlik_zarar",
                    "drawdown_pct": round(-daily_pnl / self._day_start_balance * 100, 2),
                    "balance": round(self.balance, 2),
                }
            return False, "kunlik_zarar_chegarasi"

        dd = (self.peak_balance - self.balance) / self.peak_balance
        if dd >= c.max_total_drawdown * c.safety_buffer:
            if self.prop_breach is None:
                self.prop_breach = {
                    "time": ts,
                    "rule": "umumiy_drawdown",
                    "drawdown_pct": round(dd * 100, 2),
                    "balance": round(self.balance, 2),
                }
            if c.mode == "prop":
                return False, "umumiy_drawdown"

        if self._consecutive_losses >= c.max_consecutive_losses:
            return False, "ketma_ket_zararlar"

        if self._trades_today >= c.max_trades_per_day:
            return False, "kunlik_savdo_limiti"

        if self.balance <= 0:
            return False, "balans_tugadi"

        return True, "ok"

    def _position_size(self, entry: float, atr: float,
                       avg_atr: Optional[float],
                       sl_mult: float) -> tuple[float, float, float]:
        """Qaytaradi: (hajm, risk_summa, stop_masofasi)"""
        c = self.cfg
        risk_pct = c.risk_per_trade

        if c.vol_scaling and avg_atr and avg_atr > 0:
            ratio = atr / avg_atr
            if ratio > 1:
                risk_pct /= ratio

        risk_pct = min(risk_pct, c.max_risk_per_trade)
        stop_distance = atr * sl_mult

        if stop_distance <= 0:
            return 0.0, 0.0, 0.0

        risk_amount = self.balance * risk_pct
        size = risk_amount / stop_distance
        return size, risk_amount, stop_distance

    # ── Ijro ────────────────────────────────────────────────

    def _open(self, i: int, signal: Signal) -> Optional[BacktestTrade]:
        """
        Pozitsiya (i)-shamchaning OCHILISH narxida ochiladi.
        Signal esa (i-1)-shamchaning yopilishida berilgan edi.
        """
        c = self.cfg
        candle = self.candles[i]

        atr = self._atr(i - 1)          # signal paytidagi ATR
        if atr is None or atr <= 0:
            return None

        avg_atr = self._avg_atr(i - 1)
        sl_mult = signal.sl_atr_mult or c.atr_multiplier

        size, risk_amount, stop_distance = self._position_size(
            candle["open"], atr, avg_atr, sl_mult)

        if size <= 0:
            return None

        # Slippage — bizga qarshi
        direction = 1 if signal.side == "long" else -1
        entry = candle["open"] * (1 + direction * c.slippage_pct)

        stop = entry - direction * stop_distance
        target = entry + direction * stop_distance * signal.rr

        entry_fee = entry * size * c.taker_fee

        trade = BacktestTrade(
            entry_time=candle["timestamp"],
            entry_price=entry,
            side=signal.side,
            size=size,
            stop_loss=stop,
            take_profit=target,
            risk_amount=risk_amount,
            reason=signal.reason,
            metadata=signal.metadata or {},
            fees=entry_fee,
        )

        self.balance -= entry_fee
        self._trades_today += 1
        return trade

    def _check_exit(self, trade: BacktestTrade, candle: dict) -> Optional[tuple]:
        """
        Shamcha ichida SL yoki TP tegdimi?

        Konservativ qoida: ikkalasi ham tegsa — SL hisoblanadi.
        Real ijroda qaysi biri oldin bo'lganini bilmaymiz, shuning
        uchun yomon holatni tanlaymiz. Bu backtestni haqiqatdan
        yaxshiroq ko'rsatmaslikka xizmat qiladi.
        """
        high, low = candle["high"], candle["low"]

        if trade.side == "long":
            hit_sl = low <= trade.stop_loss
            hit_tp = high >= trade.take_profit
        else:
            hit_sl = high >= trade.stop_loss
            hit_tp = low <= trade.take_profit

        if hit_sl:
            return trade.stop_loss, "stop_loss"
        if hit_tp:
            return trade.take_profit, "take_profit"
        return None

    def _close(self, trade: BacktestTrade, exit_price: float,
               exit_time: int, reason: str):
        c = self.cfg
        direction = 1 if trade.side == "long" else -1

        # Chiqishda ham slippage
        actual_exit = exit_price * (1 - direction * c.slippage_pct)
        exit_fee = actual_exit * trade.size * c.taker_fee

        gross = (actual_exit - trade.entry_price) * direction * trade.size
        net = gross - exit_fee

        trade.exit_price = actual_exit
        trade.exit_time = exit_time
        trade.exit_reason = reason
        trade.fees += exit_fee
        trade.pnl = net
        trade.pnl_pct = net / self.balance if self.balance > 0 else 0.0
        trade.r_multiple = (net / trade.risk_amount
                            if trade.risk_amount > 0 else 0.0)

        self.balance += net
        if self.balance > self.peak_balance:
            self.peak_balance = self.balance

        self._consecutive_losses = (self._consecutive_losses + 1
                                    if net < 0 else 0)

        self.trades.append(trade)
        self.equity_curve.append(self.balance)

    # ── Asosiy sikl ─────────────────────────────────────────

    def run(self) -> dict:
        cfg = self.cfg
        warmup = max(self.strategy.warmup, cfg.atr_period + 2)

        if len(self.candles) <= warmup + 10:
            raise ValueError(
                f"Shamchalar yetarli emas: {len(self.candles)} ta, "
                f"kamida {warmup + 10} kerak"
            )

        pending: Optional[Signal] = None

        for i in range(warmup, len(self.candles)):
            candle = self.candles[i]

            # ── 1. Ochiq pozitsiyani tekshirish ─────────────
            if self.open_trade:
                self.open_trade.bars_held += 1
                exit_info = self._check_exit(self.open_trade, candle)

                if exit_info:
                    price, reason = exit_info
                    self._close(self.open_trade, price,
                                candle["timestamp"], reason)
                    self.open_trade = None
                elif (cfg.max_bars_in_trade and
                      self.open_trade.bars_held >= cfg.max_bars_in_trade):
                    self._close(self.open_trade, candle["close"],
                                candle["timestamp"], "vaqt_tugadi")
                    self.open_trade = None

            # ── 2. Kutayotgan signalni bajarish ─────────────
            # Signal oldingi shamchada berilgan, hozir ochiladi
            if pending and not self.open_trade:
                allowed, why = self._can_trade(candle["timestamp"])
                if allowed:
                    trade = self._open(i, pending)
                    if trade:
                        self.open_trade = trade
                    else:
                        self.rejected.append({
                            "time": candle["timestamp"],
                            "reason": "hajm_hisoblanmadi",
                            "signal": pending.reason,
                        })
                else:
                    self.rejected.append({
                        "time": candle["timestamp"],
                        "reason": why,
                        "signal": pending.reason,
                    })
            pending = None

            # ── 3. Yangi signal so'rash ─────────────────────
            # Window faqat 0..i ni ko'rsatadi — kelajak yopiq
            if not self.open_trade or not cfg.one_position_at_a_time:
                w = Window(self.candles, i, self._indicator_cache)
                try:
                    signal = self.strategy.on_candle(w)
                except Exception as e:
                    self.rejected.append({
                        "time": candle["timestamp"],
                        "reason": f"strategiya_xatosi: {type(e).__name__}",
                        "signal": str(e),
                    })
                    signal = None

                if signal:
                    pending = signal

        # ── Oxirida ochiq pozitsiyani yopamiz ───────────────
        if self.open_trade:
            last = self.candles[-1]
            self._close(self.open_trade, last["close"],
                        last["timestamp"], "backtest_tugadi")
            self.open_trade = None

        return self.results()

    # ── Natija ──────────────────────────────────────────────

    def results(self) -> dict:
        trade_dicts = [asdict(t) for t in self.trades]
        duration_ms = self.candles[-1]["timestamp"] - self.candles[0]["timestamp"]
        m = M.compute_all(trade_dicts, self.equity_curve,
                          self.cfg.starting_balance, self.cfg.timeframe,
                          duration_ms=duration_ms)

        # Bozor natijasi — strategiya bundan yaxshi bo'lishi kerak
        first_close = self.candles[0]["close"]
        last_close = self.candles[-1]["close"]
        market_return = (last_close - first_close) / first_close * 100

        # Rad etish sabablarini sanaymiz
        reject_counts: Dict[str, int] = {}
        for r in self.rejected:
            reject_counts[r["reason"]] = reject_counts.get(r["reason"], 0) + 1

        return {
            "strategiya": self.strategy.describe(),
            "shamchalar": len(self.candles),
            "davr": {
                "boshi": self.candles[0]["timestamp"],
                "oxiri": self.candles[-1]["timestamp"],
            },
            "metrikalar": m,
            "savdolar": trade_dicts,
            "equity": self.equity_curve,
            "rad_etilgan": reject_counts,
            "jami_komissiya": round(sum(t.fees for t in self.trades), 2),
            "bozor_%": round(market_return, 2),
            "prop_buzilish": self.prop_breach,
            "rejim": self.cfg.mode,
        }


# ─────────────────────────────────────────────────────────────
# Train / Test bo'linishi
# ─────────────────────────────────────────────────────────────

def split_data(candles: List[dict], train_ratio: float = 0.7) -> tuple:
    """
    Ma'lumotni vaqt bo'yicha ikkiga bo'ladi.

    MUHIM: tasodifiy aralashtirmaymiz. Vaqt qatorida aralashtirish
    kelajakdan o'tmishga ma'lumot sizishiga olib keladi.
    """
    if not 0.1 <= train_ratio <= 0.9:
        raise ValueError("train_ratio 0.1 va 0.9 orasida bo'lishi kerak")

    ordered = sorted(candles, key=lambda c: c["timestamp"])
    split_at = int(len(ordered) * train_ratio)
    return ordered[:split_at], ordered[split_at:]


def run_split_test(candles: List[dict], strategy_factory,
                   config: Optional[BacktestConfig] = None,
                   train_ratio: float = 0.7) -> dict:
    """
    Strategiyani train va test qismlarida alohida sinaydi.

    strategy_factory — har safar YANGI strategiya obyektini qaytaradigan
    funksiya. Bu muhim: strategiya ichki holatini saqlashi mumkin, va
    train'dagi holat test'ga o'tmasligi kerak.

    Natijani qanday o'qish:
      • Train yaxshi, test yaxshi  -> umid bor
      • Train yaxshi, test yomon   -> OVERFITTING, strategiya yaroqsiz
      • Ikkalasi ham yomon         -> strategiya ishlamaydi
    """
    train, test = split_data(candles, train_ratio)

    bt_train = Backtest(train, strategy_factory(), config)
    res_train = bt_train.run()

    bt_test = Backtest(test, strategy_factory(), config)
    res_test = bt_test.run()

    m_tr = res_train["metrikalar"]
    m_te = res_test["metrikalar"]

    verdict = _judge(m_tr, m_te)

    return {
        "train": res_train,
        "test": res_test,
        "xulosa": verdict,
    }


def _judge(m_train: dict, m_test: dict) -> dict:
    """Train va test natijalarini solishtirib xulosa chiqaradi."""

    if m_test.get("savdolar", 0) < 10:
        return {
            "holat": "yetarsiz",
            "xabar": f"Test qismida faqat {m_test.get('savdolar', 0)} ta "
                     f"savdo — xulosa chiqarish uchun kam",
        }

    tr_pf = m_train.get("profit_factor") or 0
    te_pf = m_test.get("profit_factor") or 0
    tr_ret = m_train.get("foyda_%", 0)
    te_ret = m_test.get("foyda_%", 0)

    if isinstance(tr_pf, float) and math.isinf(tr_pf):
        tr_pf = 99
    if isinstance(te_pf, float) and math.isinf(te_pf):
        te_pf = 99

    # Overfitting belgisi: train yaxshi, test sezilarli yomon
    degradation = (tr_pf - te_pf) / tr_pf if tr_pf > 0 else 0

    if te_ret <= 0 and tr_ret > 0:
        return {
            "holat": "overfitting",
            "xabar": f"Train'da +{tr_ret:.1f}%, test'da {te_ret:.1f}% — "
                     f"strategiya o'tmishga moslashgan, kelajakda ishlamaydi",
        }

    if degradation > 0.5:
        return {
            "holat": "shubhali",
            "xabar": f"Profit factor train'da {tr_pf:.2f}, test'da "
                     f"{te_pf:.2f} — {degradation:.0%} pasayish",
        }

    if te_ret > 0 and te_pf > 1.2:
        return {
            "holat": "umidli",
            "xabar": f"Test qismida +{te_ret:.1f}%, PF {te_pf:.2f} — "
                     f"walk-forward tahlilga arziydi",
        }

    return {
        "holat": "ishlamaydi",
        "xabar": f"Test qismida natija yetarli emas "
                 f"({te_ret:+.1f}%, PF {te_pf:.2f})",
    }


# ─────────────────────────────────────────────────────────────
# Walk-forward
# ─────────────────────────────────────────────────────────────

def walk_forward(candles: List[dict], strategy_factory,
                 config: Optional[BacktestConfig] = None,
                 n_windows: int = 5, train_ratio: float = 0.7) -> dict:
    """
    Ma'lumotni bir necha oynaga bo'lib, har birida train/test qiladi.

    Bu bitta split'dan ancha halolroq: strategiya turli bozor
    sharoitlarida (bull, bear, sideways) sinaladi.

    Agar 5 oynadan 4 tasida ijobiy bo'lsa — kuchli belgi.
    1-2 tasida bo'lsa — omad, strategiya emas.
    """
    ordered = sorted(candles, key=lambda c: c["timestamp"])
    window_size = len(ordered) // n_windows

    if window_size < 500:
        raise ValueError(
            f"Har oyna uchun {window_size} shamcha juda kam. "
            f"n_windows'ni kamaytiring yoki ko'proq ma'lumot yuklang."
        )

    results = []
    for k in range(n_windows):
        start = k * window_size
        end = start + window_size if k < n_windows - 1 else len(ordered)
        chunk = ordered[start:end]

        try:
            r = run_split_test(chunk, strategy_factory, config, train_ratio)
            results.append({
                "oyna": k + 1,
                "test_foyda_%": r["test"]["metrikalar"].get("foyda_%", 0),
                "test_savdolar": r["test"]["metrikalar"].get("savdolar", 0),
                "test_pf": r["test"]["metrikalar"].get("profit_factor"),
                "holat": r["xulosa"]["holat"],
            })
        except ValueError as e:
            results.append({"oyna": k + 1, "xato": str(e)})

    valid = [r for r in results if "xato" not in r]
    positive = [r for r in valid if r.get("test_foyda_%", 0) > 0]

    return {
        "oynalar": results,
        "ijobiy": f"{len(positive)}/{len(valid)}",
        "o'rtacha_foyda_%": (round(sum(r["test_foyda_%"] for r in valid)
                                   / len(valid), 2) if valid else 0),
        "barqaror": len(positive) >= len(valid) * 0.7 if valid else False,
    }
