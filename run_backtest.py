"""
run_backtest.py — Backtest ishga tushiruvchi
============================================

Ishlatish:
    python run_backtest.py --strategy ema_cross
    python run_backtest.py --strategy ema_cross --fast 20 --slow 50
    python run_backtest.py --strategy ema_cross --split
    python run_backtest.py --strategy ema_cross --walk-forward
    python run_backtest.py --strategy buy_and_hold
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

from core.storage import CandleStore, ms_to_iso
from core.strategy import EmaCrossStrategy, BuyAndHoldStrategy
from analysis.backtest import (
    Backtest, BacktestConfig, run_split_test, walk_forward
)
from analysis.metrics import format_report


STRATEGIES = {
    "ema_cross": EmaCrossStrategy,
    "buy_and_hold": BuyAndHoldStrategy,
}


def load_candles(db_path: str, symbol: str, timeframe: str) -> list:
    store = CandleStore(db_path)
    candles = store.get_candles(symbol, timeframe, closed_only=True)
    stats = store.stats(symbol, timeframe)
    store.close()

    if not candles:
        print(f"❌ Ma'lumot topilmadi: {symbol} {timeframe}")
        print("   Avval yuklang: python -m core.collector backfill --days 365")
        sys.exit(1)

    print(f"\n📂 Ma'lumot yuklandi")
    print(f"   {symbol} {timeframe}")
    print(f"   {len(candles):,} ta shamcha")
    print(f"   {ms_to_iso(candles[0]['timestamp'])} → "
          f"{ms_to_iso(candles[-1]['timestamp'])}")

    coverage = float(stats["qoplash"].rstrip("%"))
    if coverage < 99.0:
        print(f"   ⚠️  Qoplash {stats['qoplash']} — "
              f"'python -m core.collector repair' ishlating")
    else:
        print(f"   ✅ Qoplash {stats['qoplash']}")

    return candles


def build_strategy_factory(name: str, params: dict):
    cls = STRATEGIES.get(name)
    if cls is None:
        print(f"❌ Noma'lum strategiya: {name}")
        print(f"   Mavjudlari: {', '.join(STRATEGIES)}")
        sys.exit(1)

    valid = {k: v for k, v in params.items() if v is not None}

    def factory():
        try:
            return cls(**valid)
        except TypeError:
            return cls()

    return factory


def print_context(res: dict):
    """Bozor bilan solishtirish va prop qoidasi holati."""
    strat = res["metrikalar"].get("foyda_%", 0)
    market = res.get("bozor_%", 0)
    diff = strat - market

    print("  SOLISHTIRISH")
    print("  " + "-" * 48)
    print(f"  {'Strategiya':<26} {strat:>17.2f}%")
    print(f"  {'Bozor (BTC narxi)':<26} {market:>17.2f}%")
    print(f"  {'Farq':<26} {diff:>+17.2f}%")

    breach = res.get("prop_buzilish")
    if breach:
        when = ms_to_iso(breach["time"])
        print(f"\n  PROP CHALLENGE: ❌ YIQILDI")
        print(f"  {when} — {breach['rule']} "
              f"({breach['drawdown_pct']}%, balans ${breach['balance']})")
        if res.get("rejim") == "research":
            print("  (research rejimi: savdo davom ettirildi)")
    else:
        print(f"\n  PROP CHALLENGE: ✅ qoidalar buzilmadi")
    print()


def print_split_result(result: dict):
    print(format_report(result["train"]["metrikalar"], "TRAIN (70%)"))
    print_context(result["train"])
    print(format_report(result["test"]["metrikalar"], "TEST (30%) — asosiy"))
    print_context(result["test"])

    v = result["xulosa"]
    icons = {
        "umidli": "✅",
        "shubhali": "⚠️ ",
        "overfitting": "❌",
        "ishlamaydi": "❌",
        "yetarsiz": "ℹ️ ",
    }
    icon = icons.get(v["holat"], "  ")

    print("\n" + "=" * 52)
    print(f"  XULOSA: {icon} {v['holat'].upper()}")
    print("=" * 52)
    print(f"  {v['xabar']}")
    print("=" * 52 + "\n")

    if v["holat"] == "overfitting":
        print("  Nima qilish kerak:")
        print("  • Parametrlarni kamaytiring (kam parametr = kam moslashish)")
        print("  • Strategiyani soddalashtiring")
        print("  • Boshqa g'oyani sinab ko'ring\n")


def main():
    load_dotenv()

    p = argparse.ArgumentParser(description="Backtest ishga tushiruvchi")
    p.add_argument("--strategy", default="ema_cross",
                   help=f"Mavjud: {', '.join(STRATEGIES)}")
    p.add_argument("--symbol", default=os.getenv("SYMBOL", "BTC/USDT"))
    p.add_argument("--timeframe", default=os.getenv("TIMEFRAME", "15m"))
    p.add_argument("--db", default="data/market.db")

    p.add_argument("--balance", type=float,
                   default=float(os.getenv("STARTING_BALANCE", 500)))
    p.add_argument("--risk", type=float,
                   default=float(os.getenv("RISK_PER_TRADE", 0.01)))

    p.add_argument("--split", action="store_true",
                   help="Train/test bo'lib sinash (tavsiya etiladi)")
    p.add_argument("--walk-forward", action="store_true",
                   help="Bir necha oynada sinash (eng halol)")
    p.add_argument("--windows", type=int, default=5)

    # Strategiya parametrlari
    p.add_argument("--fast", type=int)
    p.add_argument("--slow", type=int)
    p.add_argument("--rr", type=float)

    p.add_argument("--mode", choices=["research", "prop"], default="research",
                   help="research: butun davrni sinaydi | "
                        "prop: drawdown buzilsa to'xtaydi")
    p.add_argument("--save", help="Natijani JSON faylga saqlash")

    args = p.parse_args()

    candles = load_candles(args.db, args.symbol, args.timeframe)

    cfg = BacktestConfig(
        starting_balance=args.balance,
        risk_per_trade=args.risk,
        timeframe=args.timeframe,
        mode=args.mode,
    )

    factory = build_strategy_factory(args.strategy, {
        "fast": args.fast,
        "slow": args.slow,
        "rr": args.rr,
    })

    print(f"\n⚙️  Strategiya: {factory().describe()}")
    print(f"   Balans: ${cfg.starting_balance:,.2f}  |  "
          f"Risk: {cfg.risk_per_trade:.1%}  |  "
          f"Komissiya: {cfg.taker_fee:.2%}  |  "
          f"Rejim: {cfg.mode}")

    # ── Walk-forward ────────────────────────────────────────
    if args.walk_forward:
        print(f"\n🔄 Walk-forward tahlil ({args.windows} oyna)...")
        wf = walk_forward(candles, factory, cfg, n_windows=args.windows)

        print("\n" + "=" * 52)
        print("  WALK-FORWARD NATIJASI")
        print("=" * 52)
        print(f"  {'Oyna':<8}{'Foyda':>12}{'Savdolar':>12}{'Holat':>18}")
        print("  " + "-" * 48)

        for w in wf["oynalar"]:
            if "xato" in w:
                print(f"  {w['oyna']:<8}{'xato':>12}")
                continue
            print(f"  {w['oyna']:<8}{w['test_foyda_%']:>11.2f}%"
                  f"{w['test_savdolar']:>12}{w['holat']:>18}")

        print("  " + "-" * 48)
        print(f"  Ijobiy oynalar:    {wf['ijobiy']}")
        avg_key = "o'rtacha_foyda_%"
        stable = "✅ HA" if wf["barqaror"] else "❌ YO'Q"
        print(f"  O'rtacha foyda:    {wf[avg_key]:+.2f}%")
        print(f"  Barqarormi:        {stable}")
        print("=" * 52 + "\n")

        if args.save:
            with open(args.save, "w", encoding="utf-8") as f:
                json.dump(wf, f, indent=2, ensure_ascii=False)
            print(f"💾 Saqlandi: {args.save}\n")
        return

    # ── Train/test split ────────────────────────────────────
    if args.split:
        print("\n🔬 Train/test bo'linishi bilan sinash...")
        result = run_split_test(candles, factory, cfg)
        print_split_result(result)

        if args.save:
            out = {
                "train": result["train"]["metrikalar"],
                "test": result["test"]["metrikalar"],
                "xulosa": result["xulosa"],
            }
            with open(args.save, "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2, ensure_ascii=False)
            print(f"💾 Saqlandi: {args.save}\n")
        return

    # ── Oddiy backtest ──────────────────────────────────────
    print("\n▶️  Backtest ishlamoqda...")
    bt = Backtest(candles, factory(), cfg)
    res = bt.run()

    print(format_report(res["metrikalar"]))
    print_context(res)

    if res["rad_etilgan"]:
        print("  RAD ETILGAN SIGNALLAR")
        print("  " + "-" * 48)
        for reason, count in sorted(res["rad_etilgan"].items(),
                                    key=lambda x: -x[1]):
            print(f"  {reason:<30} {count:>6}")
        print()

    print(f"  Jami komissiya: ${res['jami_komissiya']:,.2f}\n")

    print("  ⚠️  Bu bitta o'tish natijasi. Ishonchli xulosa uchun:")
    print("     python run_backtest.py --strategy "
          f"{args.strategy} --split\n")

    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            json.dump({"metrikalar": res["metrikalar"],
                       "rad_etilgan": res["rad_etilgan"]},
                      f, indent=2, ensure_ascii=False)
        print(f"💾 Saqlandi: {args.save}\n")


if __name__ == "__main__":
    main()
