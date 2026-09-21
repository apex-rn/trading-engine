"""
metrics.py — Natija o'lchovlari
================================

Backtest natijasini baholash uchun metrikalar.

Muhim: bitta metrikaga qarab qaror qilinmaydi. Yuqori foyda katta
drawdown bilan kelsa, strategiya yaroqsiz. Shuning uchun hammasi
birga ko'riladi.
"""

import math
from typing import List, Dict, Optional


# Yiliga necha savdo davri bor — Sharpe'ni yillikka keltirish uchun
PERIODS_PER_YEAR = {
    "1m": 525_600,
    "5m": 105_120,
    "15m": 35_040,
    "30m": 17_520,
    "1h": 8_760,
    "4h": 2_190,
    "1d": 365,
}


def max_drawdown(equity: List[float]) -> Dict[str, float]:
    """
    Eng katta pasayish — cho'qqidan tubgacha.

    Bu eng muhim risk metrikasi. 50% drawdown'dan qaytish uchun
    100% foyda kerak, shuning uchun u foydadan muhimroq.
    """
    if len(equity) < 2:
        return {"max_dd_pct": 0.0, "max_dd_abs": 0.0,
                "peak": equity[0] if equity else 0.0, "trough": 0.0}

    peak = equity[0]
    max_dd_pct = 0.0
    max_dd_abs = 0.0
    peak_at_max = equity[0]
    trough_at_max = equity[0]

    for v in equity:
        if v > peak:
            peak = v
        dd_abs = peak - v
        dd_pct = dd_abs / peak if peak > 0 else 0.0
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct
            max_dd_abs = dd_abs
            peak_at_max = peak
            trough_at_max = v

    return {
        "max_dd_pct": max_dd_pct,
        "max_dd_abs": max_dd_abs,
        "peak": peak_at_max,
        "trough": trough_at_max,
    }


def sharpe_ratio(returns: List[float],
                 periods_per_year: float,
                 risk_free: float = 0.0) -> Optional[float]:
    """
    Sharpe — risk birligiga to'g'ri keladigan foyda (yillik).

    returns — har SAVDO bo'yicha daromad foizlari.
    periods_per_year — yiliga necha savdo (davr uzunligidan hisoblanadi).

    > 1.0  yaxshi
    > 2.0  juda yaxshi
    < 0.5  strategiya risk uchun yetarli haq bermaydi

    Kamida 30 savdo bo'lmasa, Sharpe ishonchsiz.
    """
    if len(returns) < 2 or periods_per_year <= 0:
        return None

    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(var)

    if std == 0:
        return None

    return (mean - risk_free) / std * math.sqrt(periods_per_year)


def sortino_ratio(returns: List[float],
                  periods_per_year: float) -> Optional[float]:
    """
    Sortino — faqat pastga qarab volatillikni jazolaydi.
    Sharpe'dan adolatliroq, chunki yuqoriga sakrash yomon emas.
    """
    if len(returns) < 2 or periods_per_year <= 0:
        return None

    mean = sum(returns) / len(returns)
    downside = [r for r in returns if r < 0]
    if not downside:
        return None

    dstd = math.sqrt(sum(r ** 2 for r in downside) / len(returns))
    if dstd == 0:
        return None

    return mean / dstd * math.sqrt(periods_per_year)


def profit_factor(pnls: List[float]) -> Optional[float]:
    """
    Yalpi foyda / yalpi zarar.

    > 1.5  yaxshi
    > 2.0  kuchli
    < 1.0  zarar keltiradi
    """
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))

    if gross_loss == 0:
        return None if gross_win == 0 else float("inf")
    return gross_win / gross_loss


def expectancy(pnls: List[float]) -> float:
    """Bitta savdodan kutilayotgan o'rtacha natija ($)."""
    return sum(pnls) / len(pnls) if pnls else 0.0


def expectancy_r(r_multiples: List[float]) -> float:
    """
    R birligida kutilma. R = bitta savdodagi risk.

    +0.2R degani: har savdoda risk qilgan summangizning 20% ini
    o'rtacha yutasiz. Bu $ dan ko'ra ishonchliroq o'lchov, chunki
    depozit hajmiga bog'liq emas.
    """
    return sum(r_multiples) / len(r_multiples) if r_multiples else 0.0


def win_rate(pnls: List[float]) -> float:
    if not pnls:
        return 0.0
    return len([p for p in pnls if p > 0]) / len(pnls)


def longest_losing_streak(pnls: List[float]) -> int:
    """Eng uzun ketma-ket zarar — psixologik chidamlilik o'lchovi."""
    longest = current = 0
    for p in pnls:
        if p <= 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def calmar_ratio(total_return_pct: float, max_dd_pct: float) -> Optional[float]:
    """Foyda / maksimal drawdown. > 3 yaxshi hisoblanadi."""
    if max_dd_pct == 0:
        return None
    return total_return_pct / max_dd_pct


def compute_all(trades: List[dict], equity: List[float],
                starting_balance: float, timeframe: str = "15m",
                duration_ms: Optional[int] = None) -> dict:
    """Hamma metrikalarni bir marta hisoblaydi."""

    if not trades:
        return {
            "savdolar": 0,
            "xabar": "Savdo bo'lmadi — strategiya signal bermadi "
                     "yoki risk filtrlari hammasini rad etdi",
        }

    pnls = [t["pnl"] for t in trades]
    r_multiples = [t.get("r_multiple", 0.0) for t in trades]

    final = equity[-1] if equity else starting_balance
    total_return = (final - starting_balance) / starting_balance

    # Savdolararo daromad foizlari — Sharpe uchun
    returns = []
    for i in range(1, len(equity)):
        if equity[i - 1] > 0:
            returns.append((equity[i] - equity[i - 1]) / equity[i - 1])

    dd = max_drawdown(equity)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    # Yiliga savdolar soni — Sharpe'ni to'g'ri yillikka keltirish uchun
    years = (duration_ms / (365.25 * 86_400_000)) if duration_ms else 1.0
    trades_per_year = len(returns) / years if years > 0 else 0

    pf = profit_factor(pnls)
    sharpe = sharpe_ratio(returns, trades_per_year)
    sortino = sortino_ratio(returns, trades_per_year)

    return {
        # Natija
        "boshlang'ich": round(starting_balance, 2),
        "yakuniy": round(final, 2),
        "foyda_$": round(final - starting_balance, 2),
        "foyda_%": round(total_return * 100, 2),

        # Savdolar
        "savdolar": len(trades),
        "yutuq": len(wins),
        "zarar": len(losses),
        "win_rate_%": round(win_rate(pnls) * 100, 1),

        # Sifat
        "profit_factor": round(pf, 2) if pf and pf != float("inf") else pf,
        "kutilma_$": round(expectancy(pnls), 2),
        "kutilma_R": round(expectancy_r(r_multiples), 3),
        "o'rtacha_yutuq": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "o'rtacha_zarar": round(sum(losses) / len(losses), 2) if losses else 0.0,

        # Risk
        "max_drawdown_%": round(dd["max_dd_pct"] * 100, 2),
        "max_drawdown_$": round(dd["max_dd_abs"], 2),
        "eng_uzun_zarar_seriyasi": longest_losing_streak(pnls),

        # Risk bilan tuzatilgan
        "sharpe": round(sharpe, 2) if sharpe is not None else None,
        "sortino": round(sortino, 2) if sortino is not None else None,
        "calmar": (round(calmar_ratio(total_return * 100,
                                      dd["max_dd_pct"] * 100), 2)
                   if dd["max_dd_pct"] > 0 else None),
    }


def format_report(m: dict, title: str = "BACKTEST NATIJASI") -> str:
    """Metrikalarni o'qiladigan matnga aylantiradi."""

    if m.get("savdolar", 0) == 0:
        return f"\n{title}\n{'=' * 52}\n  {m.get('xabar', 'Ma\'lumot yo\'q')}\n"

    lines = [f"\n{title}", "=" * 52]

    def row(label, value, suffix=""):
        if value is None:
            value = "—"
        lines.append(f"  {label:<26} {str(value):>18}{suffix}")

    lines.append("\n  NATIJA")
    lines.append("  " + "-" * 48)
    row("Boshlang'ich balans", f"${m['boshlang\'ich']:,.2f}")
    row("Yakuniy balans", f"${m['yakuniy']:,.2f}")
    row("Foyda", f"${m['foyda_$']:,.2f}")
    row("Foyda foizi", f"{m['foyda_%']:+.2f}%")

    lines.append("\n  SAVDOLAR")
    lines.append("  " + "-" * 48)
    row("Jami savdolar", m["savdolar"])
    row("Yutuq / Zarar", f"{m['yutuq']} / {m['zarar']}")
    row("Win rate", f"{m['win_rate_%']}%")
    row("O'rtacha yutuq", f"${m['o\'rtacha_yutuq']:,.2f}")
    row("O'rtacha zarar", f"${m['o\'rtacha_zarar']:,.2f}")

    lines.append("\n  SIFAT")
    lines.append("  " + "-" * 48)
    row("Profit factor", m["profit_factor"])
    row("Kutilma (savdo boshiga)", f"${m['kutilma_$']:,.2f}")
    row("Kutilma (R birligida)", f"{m['kutilma_R']:+.3f}R")

    lines.append("\n  RISK")
    lines.append("  " + "-" * 48)
    row("Maksimal drawdown", f"{m['max_drawdown_%']:.2f}%")
    row("Drawdown ($)", f"${m['max_drawdown_$']:,.2f}")
    row("Eng uzun zarar seriyasi", m["eng_uzun_zarar_seriyasi"])
    row("Sharpe ratio", m["sharpe"])
    row("Sortino ratio", m["sortino"])
    row("Calmar ratio", m["calmar"])

    lines.append("=" * 52)

    # Ogohlantirishlar
    warnings = []
    if m["savdolar"] < 30:
        warnings.append(f"Faqat {m['savdolar']} ta savdo — statistik "
                        f"ishonchsiz (kamida 30 kerak)")
    if m["max_drawdown_%"] > 30:
        warnings.append(f"Drawdown {m['max_drawdown_%']:.0f}% — juda yuqori")
    if m["profit_factor"] and m["profit_factor"] != float("inf") \
            and m["profit_factor"] < 1.0:
        warnings.append("Profit factor < 1.0 — strategiya zarar keltiradi")

    if warnings:
        lines.append("\n  OGOHLANTIRISH")
        for w in warnings:
            lines.append(f"  ! {w}")
        lines.append("")

    return "\n".join(lines)
