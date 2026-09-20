"""
risk_engine.py — Trading botning risk yadrosi
==============================================
Vazifasi:
  1. Bozor volatilligiga qarab pozitsiya hajmini hisoblash (ATR-based sizing)
  2. Prop firma qoidalarini (kunlik / umumiy drawdown) kuzatish
  3. Yangilik va risk filtrlari
  4. Har savdoni jurnalga yozish va statistika chiqarish

Bu modul SIGNAL BERMAYDI. U faqat "signal kelganda qancha hajm bilan
kirish mumkin va umuman kirish mumkinmi" degan savolga javob beradi.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from typing import List, Optional
import json
import os
import math


# ─────────────────────────────────────────────────────────────
# 1. SOZLAMALAR
# ─────────────────────────────────────────────────────────────

@dataclass
class RiskConfig:
    """Botning risk qoidalari. Hammasi shu yerda, kod ichida emas."""

    # Asosiy risk
    risk_per_trade: float = 0.01       # har savdoda kapitalning 1%
    max_risk_per_trade: float = 0.02   # hech qachon 2% dan oshmasin

    # Prop firma chegaralari (FTMO standarti)
    max_daily_loss: float = 0.05       # kunlik 5%
    max_total_drawdown: float = 0.10   # umumiy 10%
    safety_buffer: float = 0.8         # chegaraning 80% ida to'xtaymiz

    # Volatillikka moslashish
    atr_period: int = 14
    atr_multiplier: float = 2.0        # stop-loss = ATR * 2
    vol_scaling: bool = True           # volatillik yuqori -> hajm kichik

    # Kunlik cheklovlar
    max_trades_per_day: int = 5
    max_consecutive_losses: int = 3    # 3 ta ketma-ket zarardan keyin to'xtash

    # Yangilik filtri
    news_blackout_minutes: int = 30    # muhim yangilikdan oldin/keyin savdo yo'q


# ─────────────────────────────────────────────────────────────
# 2. SAVDO YOZUVI
# ─────────────────────────────────────────────────────────────

@dataclass
class Trade:
    timestamp: str
    symbol: str
    side: str                 # "long" yoki "short"
    entry: float
    stop_loss: float
    take_profit: Optional[float]
    size: float
    risk_amount: float
    atr: float
    reason: str = ""          # nega kirdim
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    status: str = "open"      # open / closed

    def close(self, exit_price: float):
        self.exit_price = exit_price
        direction = 1 if self.side == "long" else -1
        self.pnl = (exit_price - self.entry) * direction * self.size
        self.status = "closed"
        return self.pnl


# ─────────────────────────────────────────────────────────────
# 3. ATR — volatillik o'lchovi
# ─────────────────────────────────────────────────────────────

def calculate_atr(candles: List[dict], period: int = 14) -> float:
    """
    ATR (Average True Range) — bozor qanchalik notinchligini ko'rsatadi.
    candles: [{"high": float, "low": float, "close": float}, ...]
    Eng eski birinchi, eng yangi oxirgi.
    """
    if len(candles) < period + 1:
        raise ValueError(f"Kamida {period + 1} ta shamcha kerak, {len(candles)} ta berildi")

    true_ranges = []
    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )
        true_ranges.append(tr)

    return sum(true_ranges[-period:]) / period


# ─────────────────────────────────────────────────────────────
# 4. RISK DVIGATELI
# ─────────────────────────────────────────────────────────────

class RiskEngine:

    def __init__(self, starting_balance: float, config: RiskConfig = None,
                 journal_path: str = "trades.json"):
        self.starting_balance = starting_balance
        self.balance = starting_balance
        self.peak_balance = starting_balance
        self.config = config or RiskConfig()
        self.journal_path = journal_path

        self.trades: List[Trade] = []
        self.day_start_balance = starting_balance
        self.current_day: date = datetime.now().date()
        self.consecutive_losses = 0

        self._load_journal()

    # ── Kunlik hisobni yangilash ────────────────────────────
    def _roll_day_if_needed(self):
        today = datetime.now().date()
        if today != self.current_day:
            self.current_day = today
            self.day_start_balance = self.balance
            self.consecutive_losses = 0

    # ── Savdo qilish mumkinmi? ──────────────────────────────
    def can_trade(self, news_soon: bool = False) -> tuple[bool, str]:
        """
        Savdo oldidan tekshiriladigan hamma to'siqlar.
        Qaytaradi: (mumkinmi, sabab)
        """
        self._roll_day_if_needed()
        c = self.config

        # Kunlik zarar chegarasi
        daily_pnl = self.balance - self.day_start_balance
        daily_limit = self.day_start_balance * c.max_daily_loss * c.safety_buffer
        if daily_pnl <= -daily_limit:
            return False, f"Kunlik zarar chegarasi: {daily_pnl:.2f} / -{daily_limit:.2f}"

        # Umumiy drawdown
        drawdown = (self.peak_balance - self.balance) / self.peak_balance
        dd_limit = c.max_total_drawdown * c.safety_buffer
        if drawdown >= dd_limit:
            return False, f"Umumiy drawdown: {drawdown:.1%} / {dd_limit:.1%}"

        # Ketma-ket zararlar
        if self.consecutive_losses >= c.max_consecutive_losses:
            return False, f"{self.consecutive_losses} ta ketma-ket zarar — bugunga to'xtash"

        # Kunlik savdo soni
        today_trades = [
            t for t in self.trades
            if datetime.fromisoformat(t.timestamp).date() == self.current_day
        ]
        if len(today_trades) >= c.max_trades_per_day:
            return False, f"Kunlik limit: {len(today_trades)} ta savdo"

        # Yangilik filtri
        if news_soon:
            return False, "Muhim yangilik yaqin — savdo to'xtatilgan"

        return True, "OK"

    # ── Pozitsiya hajmini hisoblash ─────────────────────────
    def position_size(self, entry: float, atr: float,
                      avg_atr: Optional[float] = None) -> dict:
        """
        Volatillikka moslashgan hajm.

        Asosiy formula:
            hajm = (kapital * risk%) / stop_masofasi

        Agar hozirgi ATR o'rtachadan yuqori bo'lsa (bozor notinch),
        risk foizi avtomatik kamayadi.
        """
        c = self.config

        # 1. Bazaviy risk foizi
        risk_pct = c.risk_per_trade

        # 2. Volatillikka moslashish
        vol_ratio = 1.0
        if c.vol_scaling and avg_atr and avg_atr > 0:
            vol_ratio = atr / avg_atr
            if vol_ratio > 1:
                # Bozor notinch -> riskni kamaytiramiz
                risk_pct = risk_pct / vol_ratio

        risk_pct = min(risk_pct, c.max_risk_per_trade)

        # 3. Stop-loss masofasi
        stop_distance = atr * c.atr_multiplier
        if stop_distance <= 0:
            raise ValueError("Stop masofasi noldan katta bo'lishi kerak")

        # 4. Hajm
        risk_amount = self.balance * risk_pct
        size = risk_amount / stop_distance

        return {
            "size": size,
            "risk_amount": risk_amount,
            "risk_pct": risk_pct,
            "stop_distance": stop_distance,
            "stop_long": entry - stop_distance,
            "stop_short": entry + stop_distance,
            "vol_ratio": vol_ratio,
        }

    # ── Savdoni ochish ──────────────────────────────────────
    def open_trade(self, symbol: str, side: str, entry: float, atr: float,
                   avg_atr: Optional[float] = None, rr: float = 2.0,
                   reason: str = "", news_soon: bool = False) -> Optional[Trade]:

        allowed, why = self.can_trade(news_soon=news_soon)
        if not allowed:
            print(f"❌ Savdo rad etildi: {why}")
            return None

        calc = self.position_size(entry, atr, avg_atr)
        stop = calc["stop_long"] if side == "long" else calc["stop_short"]

        direction = 1 if side == "long" else -1
        take_profit = entry + direction * calc["stop_distance"] * rr

        trade = Trade(
            timestamp=datetime.now().isoformat(),
            symbol=symbol,
            side=side,
            entry=entry,
            stop_loss=stop,
            take_profit=take_profit,
            size=calc["size"],
            risk_amount=calc["risk_amount"],
            atr=atr,
            reason=reason,
        )
        self.trades.append(trade)
        self._save_journal()

        print(f"✅ {side.upper()} {symbol} @ {entry:.4f}")
        print(f"   Hajm: {calc['size']:.4f} | Risk: ${calc['risk_amount']:.2f} "
              f"({calc['risk_pct']:.2%})")
        print(f"   SL: {stop:.4f} | TP: {take_profit:.4f} | R:R = 1:{rr}")
        if calc["vol_ratio"] != 1.0:
            print(f"   Volatillik koeffitsienti: {calc['vol_ratio']:.2f}")

        return trade

    # ── Savdoni yopish ──────────────────────────────────────
    def close_trade(self, trade: Trade, exit_price: float):
        pnl = trade.close(exit_price)
        self.balance += pnl

        if self.balance > self.peak_balance:
            self.peak_balance = self.balance

        self.consecutive_losses = self.consecutive_losses + 1 if pnl < 0 else 0

        self._save_journal()
        sign = "🟢" if pnl >= 0 else "🔴"
        print(f"{sign} Yopildi {trade.symbol} @ {exit_price:.4f} | PnL: ${pnl:+.2f} "
              f"| Balans: ${self.balance:.2f}")
        return pnl

    # ── Statistika ──────────────────────────────────────────
    def stats(self) -> dict:
        closed = [t for t in self.trades if t.status == "closed"]
        if not closed:
            return {"savdolar": 0, "xabar": "Yopilgan savdo yo'q"}

        wins = [t for t in closed if t.pnl > 0]
        losses = [t for t in closed if t.pnl <= 0]

        gross_win = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))

        return {
            "savdolar": len(closed),
            "yutuq": len(wins),
            "zarar": len(losses),
            "win_rate": f"{len(wins) / len(closed):.1%}",
            "o'rtacha_yutuq": f"${gross_win / len(wins):.2f}" if wins else "$0",
            "o'rtacha_zarar": f"${gross_loss / len(losses):.2f}" if losses else "$0",
            "profit_factor": f"{gross_win / gross_loss:.2f}" if gross_loss else "∞",
            "umumiy_pnl": f"${self.balance - self.starting_balance:+.2f}",
            "balans": f"${self.balance:.2f}",
            "max_drawdown": f"{(self.peak_balance - self.balance) / self.peak_balance:.1%}",
        }

    # ── Jurnal ──────────────────────────────────────────────
    def _save_journal(self):
        data = {
            "starting_balance": self.starting_balance,
            "balance": self.balance,
            "peak_balance": self.peak_balance,
            "trades": [asdict(t) for t in self.trades],
        }
        with open(self.journal_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _load_journal(self):
        if not os.path.exists(self.journal_path):
            return
        try:
            with open(self.journal_path, encoding="utf-8") as f:
                data = json.load(f)
            self.starting_balance = data["starting_balance"]
            self.balance = data["balance"]
            self.peak_balance = data["peak_balance"]
            self.trades = [Trade(**t) for t in data["trades"]]
            print(f"📂 Jurnal yuklandi: {len(self.trades)} ta savdo, "
                  f"balans ${self.balance:.2f}")
        except Exception as e:
            print(f"⚠️  Jurnal yuklanmadi: {e}")


# ─────────────────────────────────────────────────────────────
# 5. NAMUNA
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # $500 depozit, prop firma qoidalari bilan
    engine = RiskEngine(
        starting_balance=500,
        config=RiskConfig(risk_per_trade=0.01),
        journal_path="demo_trades.json",
    )

    # Soxta shamchalar (real ishda Binance API'dan keladi)
    candles = [
        {"high": 100 + i * 0.5, "low": 98 + i * 0.5, "close": 99 + i * 0.5}
        for i in range(20)
    ]
    atr_now = calculate_atr(candles, period=14)
    avg_atr = atr_now  # real ishda oxirgi 100 shamchaning o'rtachasi

    print(f"\nATR: {atr_now:.4f}\n")

    t = engine.open_trade(
        symbol="BTCUSDT",
        side="long",
        entry=108.5,
        atr=atr_now,
        avg_atr=avg_atr,
        rr=2.0,
        reason="Namuna savdo",
    )

    if t:
        engine.close_trade(t, exit_price=112.0)

    print("\n📊 Statistika:")
    for k, v in engine.stats().items():
        print(f"   {k}: {v}")
