"""
strategy.py — Strategiya interfeysi
====================================

Har bir strategiya BaseStrategy dan meros oladi va bitta metodni
amalga oshiradi: on_candle().

MUHIM: Strategiya faqat `window` obyekti orqali ma'lumot ko'radi.
Window kelajakdagi shamchani KO'RSATMAYDI — bu look-ahead bias'dan
himoya. Strategiya to'g'ridan-to'g'ri ma'lumot ro'yxatiga kira olmaydi.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Literal
import math


Side = Literal["long", "short"]


# ─────────────────────────────────────────────────────────────
# Signal
# ─────────────────────────────────────────────────────────────

@dataclass
class Signal:
    """Strategiya qaytaradigan qaror."""
    side: Side
    reason: str = ""                    # nega — qaror jurnaliga yoziladi
    confidence: float = 1.0             # 0..1
    sl_atr_mult: Optional[float] = None  # None -> config'dagi standart
    rr: float = 2.0                     # risk:reward nisbati
    metadata: Optional[dict] = None     # indikator qiymatlari

    def __post_init__(self):
        if self.side not in ("long", "short"):
            raise ValueError(f"Noto'g'ri side: {self.side}")
        self.confidence = max(0.0, min(1.0, self.confidence))
        if self.rr <= 0:
            raise ValueError("rr noldan katta bo'lishi kerak")


# ─────────────────────────────────────────────────────────────
# Ma'lumot oynasi — look-ahead himoyasi shu yerda
# ─────────────────────────────────────────────────────────────

class Window:
    """
    Strategiyaga beriladigan ma'lumot ko'rinishi.

    Faqat JORIY va O'TGAN yopilgan shamchalarni ko'rsatadi.
    index=-1 eng oxirgi yopilgan shamcha.

    Kelajakka kirishning imkoni yo'q — ro'yxat kesib berilgan.
    """

    __slots__ = ("_candles", "_i", "_cache")

    def __init__(self, candles: List[dict], i: int,
                 cache: Optional[dict] = None):
        # i — joriy shamcha indeksi. Faqat 0..i oralig'i ko'rinadi.
        self._candles = candles
        self._i = i
        # Indikator qatorlari keshi — backtest davomida bitta dict
        # barcha Window obyektlari orasida ulashiladi.
        self._cache = cache if cache is not None else {}

    def __len__(self) -> int:
        return self._i + 1

    # ── Qatorlar ────────────────────────────────────────────

    def closes(self, n: Optional[int] = None) -> List[float]:
        return self._series("close", n)

    def opens(self, n: Optional[int] = None) -> List[float]:
        return self._series("open", n)

    def highs(self, n: Optional[int] = None) -> List[float]:
        return self._series("high", n)

    def lows(self, n: Optional[int] = None) -> List[float]:
        return self._series("low", n)

    def volumes(self, n: Optional[int] = None) -> List[float]:
        return self._series("volume", n)

    def _series(self, key: str, n: Optional[int]) -> List[float]:
        end = self._i + 1
        start = 0 if n is None else max(0, end - n)
        return [c[key] for c in self._candles[start:end]]

    # ── Joriy shamcha ───────────────────────────────────────

    @property
    def close(self) -> float:
        return self._candles[self._i]["close"]

    @property
    def open(self) -> float:
        return self._candles[self._i]["open"]

    @property
    def high(self) -> float:
        return self._candles[self._i]["high"]

    @property
    def low(self) -> float:
        return self._candles[self._i]["low"]

    @property
    def volume(self) -> float:
        return self._candles[self._i]["volume"]

    @property
    def timestamp(self) -> int:
        return self._candles[self._i]["timestamp"]

    # ── Indikatorlar ────────────────────────────────────────

    def sma(self, period: int, offset: int = 0) -> Optional[float]:
        """Oddiy o'rtacha. offset=1 -> oldingi shamchadagi qiymat."""
        end = self._i + 1 - offset
        if end < period:
            return None
        vals = [c["close"] for c in self._candles[end - period:end]]
        return sum(vals) / period

    def _ema_series(self, period: int) -> List[Optional[float]]:
        """
        Butun EMA qatorini BIR MARTA hisoblaydi va keshlaydi.

        Look-ahead xavfsiz: EMA sababiy, ya'ni series[j] faqat
        0..j shamchalardan hisoblanadi. Biz faqat joriy indeksgacha
        o'qiymiz, shuning uchun kelajak ko'rinmaydi.

        Murakkablik: O(n) bir marta, keyin har chaqiriq O(1).
        Oldingi versiya har chaqiriqda O(n) edi -> umumiy O(n^2).
        """
        key = ("ema", period)
        series = self._cache.get(key)
        if series is not None:
            return series

        closes = [c["close"] for c in self._candles]
        n = len(closes)
        series = [None] * n
        if n >= period:
            k = 2 / (period + 1)
            e = sum(closes[:period]) / period
            series[period - 1] = e
            for j in range(period, n):
                e = closes[j] * k + e * (1 - k)
                series[j] = e

        self._cache[key] = series
        return series

    def ema(self, period: int, offset: int = 0) -> Optional[float]:
        """Eksponensial o'rtacha."""
        end = self._i + 1 - offset
        if end < period or end < 1:
            return None
        return self._ema_series(period)[end - 1]

    def atr(self, period: int = 14, offset: int = 0) -> Optional[float]:
        """Average True Range — volatillik o'lchovi."""
        end = self._i + 1 - offset
        if end < period + 1:
            return None

        trs = []
        for j in range(end - period, end):
            h = self._candles[j]["high"]
            l = self._candles[j]["low"]
            pc = self._candles[j - 1]["close"]
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        return sum(trs) / period

    def _rsi_series(self, period: int) -> List[Optional[float]]:
        """Wilder RSI qatori — bir marta hisoblanadi, sababiy."""
        key = ("rsi", period)
        series = self._cache.get(key)
        if series is not None:
            return series

        closes = [c["close"] for c in self._candles]
        n = len(closes)
        series = [None] * n

        if n > period:
            gains = [0.0] * n
            losses = [0.0] * n
            for j in range(1, n):
                ch = closes[j] - closes[j - 1]
                gains[j] = max(ch, 0.0)
                losses[j] = max(-ch, 0.0)

            avg_gain = sum(gains[1:period + 1]) / period
            avg_loss = sum(losses[1:period + 1]) / period

            def to_rsi(g, l):
                if l == 0:
                    return 100.0
                return 100 - (100 / (1 + g / l))

            series[period] = to_rsi(avg_gain, avg_loss)
            for j in range(period + 1, n):
                avg_gain = (avg_gain * (period - 1) + gains[j]) / period
                avg_loss = (avg_loss * (period - 1) + losses[j]) / period
                series[j] = to_rsi(avg_gain, avg_loss)

        self._cache[key] = series
        return series

    def rsi(self, period: int = 14, offset: int = 0) -> Optional[float]:
        """Relative Strength Index (Wilder usuli)."""
        end = self._i + 1 - offset
        if end < period + 1:
            return None
        return self._rsi_series(period)[end - 1]

    def stdev(self, period: int, offset: int = 0) -> Optional[float]:
        end = self._i + 1 - offset
        if end < period:
            return None
        vals = [c["close"] for c in self._candles[end - period:end]]
        mean = sum(vals) / period
        var = sum((v - mean) ** 2 for v in vals) / period
        return math.sqrt(var)

    def highest(self, period: int, offset: int = 0) -> Optional[float]:
        end = self._i + 1 - offset
        if end < period:
            return None
        return max(c["high"] for c in self._candles[end - period:end])

    def lowest(self, period: int, offset: int = 0) -> Optional[float]:
        end = self._i + 1 - offset
        if end < period:
            return None
        return min(c["low"] for c in self._candles[end - period:end])


# ─────────────────────────────────────────────────────────────
# Baza klass
# ─────────────────────────────────────────────────────────────

class BaseStrategy(ABC):
    """
    Har strategiya shundan meros oladi.

    warmup — strategiya ishlashi uchun kerak bo'lgan minimal
    shamchalar soni. Backtest shu sondan keyin signal so'raydi.
    """

    name: str = "unnamed"
    warmup: int = 200

    def __init__(self, **params):
        self.params = params
        for k, v in params.items():
            setattr(self, k, v)

    @abstractmethod
    def on_candle(self, w: Window) -> Optional[Signal]:
        """
        Har yopilgan shamchada chaqiriladi.
        Qaytaradi: Signal yoki None (savdo yo'q).
        """
        ...

    def on_position_open(self, w: Window, trade) -> None:
        """Pozitsiya ochilgandan keyin (ixtiyoriy)."""
        pass

    def on_position_close(self, w: Window, trade) -> None:
        """Pozitsiya yopilgandan keyin (ixtiyoriy)."""
        pass

    def describe(self) -> str:
        if not self.params:
            return self.name
        parts = [f"{k}={v}" for k, v in sorted(self.params.items())]
        return f"{self.name}({', '.join(parts)})"


# ─────────────────────────────────────────────────────────────
# Namuna strategiyalar (ochiq repoda — asl strategiya emas)
# ─────────────────────────────────────────────────────────────

class EmaCrossStrategy(BaseStrategy):
    """
    Klassik EMA kesishuvi. Bazaviy o'lchov (baseline) sifatida.

    Long:  tez EMA sekin EMA ustiga chiqdi
    Short: tez EMA sekin EMA ostiga tushdi

    Bu strategiya foyda berishi kutilmaydi — u tizimni sinash va
    boshqa strategiyalarni solishtirish uchun o'lchov nuqtasi.
    """

    name = "ema_cross"

    def __init__(self, fast: int = 50, slow: int = 200, rr: float = 2.0):
        super().__init__(fast=fast, slow=slow, rr=rr)
        self.warmup = slow + 10

    def on_candle(self, w: Window) -> Optional[Signal]:
        fast_now = w.ema(self.fast)
        slow_now = w.ema(self.slow)
        fast_prev = w.ema(self.fast, offset=1)
        slow_prev = w.ema(self.slow, offset=1)

        if None in (fast_now, slow_now, fast_prev, slow_prev):
            return None

        crossed_up = fast_prev <= slow_prev and fast_now > slow_now
        crossed_down = fast_prev >= slow_prev and fast_now < slow_now

        if crossed_up:
            return Signal(
                side="long",
                reason=f"EMA{self.fast} EMA{self.slow} ustiga kesib o'tdi",
                rr=self.rr,
                metadata={"ema_fast": round(fast_now, 2),
                          "ema_slow": round(slow_now, 2)},
            )
        if crossed_down:
            return Signal(
                side="short",
                reason=f"EMA{self.fast} EMA{self.slow} ostiga kesib o'tdi",
                rr=self.rr,
                metadata={"ema_fast": round(fast_now, 2),
                          "ema_slow": round(slow_now, 2)},
            )
        return None


class BuyAndHoldStrategy(BaseStrategy):
    """
    Eng oddiy o'lchov: birinchi shamchada long ochadi.

    Har qanday strategiya shundan yaxshi bo'lishi kerak, aks holda
    uning ma'nosi yo'q.
    """

    name = "buy_and_hold"
    warmup = 1

    def __init__(self):
        super().__init__()
        self._opened = False

    def on_candle(self, w: Window) -> Optional[Signal]:
        if self._opened:
            return None
        self._opened = True
        return Signal(side="long", reason="Buy and hold", rr=100.0)
