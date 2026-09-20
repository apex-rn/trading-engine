"""
collector.py â€” Binance'dan ma'lumot yig'uvchi
==============================================
Uchta rejim:
  1. backfill  â€” tarixiy shamchalarni yuklash
  2. repair    â€” ma'lumotdagi teshiklarni to'ldirish
  3. live      â€” real vaqtda yangi shamchalarni olish

Ishga tushirish:
    python collector.py backfill --days 365
    python collector.py repair
    python collector.py live
    python collector.py stats
"""

import argparse
import asyncio
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv

try:
    import ccxt
except ImportError:
    print("âŒ ccxt o'rnatilmagan: pip install ccxt")
    sys.exit(1)

from core.storage import CandleStore, timeframe_to_ms, ms_to_iso


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Birja ulanishi
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def build_exchange() -> ccxt.Exchange:
    load_dotenv()

    api_key = os.getenv("BINANCE_API_KEY", "").strip()
    api_secret = os.getenv("BINANCE_API_SECRET", "").strip()
    demo = os.getenv("BINANCE_TESTNET", "true").strip().lower() == "true"

    exchange = ccxt.binanceusdm({
        "apiKey": api_key,
        "secret": api_secret,
        "enableRateLimit": True,
        "options": {
            "defaultType": "future",
            "adjustForTimeDifference": True,
        },
    })

    if demo:
        exchange.enable_demo_trading(True)

    return exchange


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Yig'uvchi
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class Collector:

    def __init__(self, symbol: str, timeframe: str, db_path: str = "data/market.db"):
        self.symbol = symbol
        self.timeframe = timeframe
        self.step = timeframe_to_ms(timeframe)
        self.store = CandleStore(db_path)
        self.exchange = build_exchange()
        self._running = True
        self._stop_event: Optional[asyncio.Event] = None

    # â”€â”€ 1. Tarixiy yuklash â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def backfill(self, days: int = 365, batch_limit: int = 1000):
        """
        Oxirgi N kunlik shamchalarni yuklaydi.
        Binance bir so'rovda maksimal 1000 ta beradi, shuning uchun
        bo'laklab, vaqt bo'yicha oldinga siljib boramiz.
        """
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - days * 86_400_000

        # Agar ma'lumot allaqachon bo'lsa, eng eskisidan oldinga ketmaymiz
        existing_first = self.store.earliest_timestamp(self.symbol, self.timeframe)
        existing_last = self.store.latest_timestamp(self.symbol, self.timeframe)

        if existing_last:
            print(f"â„¹ï¸  Bazada ma'lumot bor: {ms_to_iso(existing_first)} â†’ "
                  f"{ms_to_iso(existing_last)}")
            # Yangi ma'lumotni oxirgisidan davom ettiramiz
            if existing_last > start_ms:
                start_ms = existing_last + self.step

        total_expected = max(1, (now_ms - start_ms) // self.step)
        print(f"\nðŸ“¥ Yuklash boshlandi: {self.symbol} {self.timeframe}")
        print(f"   Davr: {ms_to_iso(start_ms)} â†’ {ms_to_iso(now_ms)}")
        print(f"   Taxminan {total_expected:,} ta shamcha\n")

        cursor = start_ms
        saved_total = 0
        empty_rounds = 0

        while cursor < now_ms and self._running:
            try:
                candles = self.exchange.fetch_ohlcv(
                    self.symbol,
                    timeframe=self.timeframe,
                    since=cursor,
                    limit=batch_limit,
                )
            except ccxt.RateLimitExceeded:
                print("   â³ Rate limit â€” 10 soniya kutilmoqda...")
                time.sleep(10)
                continue
            except ccxt.NetworkError as e:
                print(f"   âš ï¸  Tarmoq xatosi: {e}. 5 soniyadan keyin qayta...")
                time.sleep(5)
                continue
            except Exception as e:
                print(f"   âŒ Xato: {type(e).__name__}: {e}")
                break

            if not candles:
                empty_rounds += 1
                if empty_rounds >= 3:
                    print("   â„¹ï¸  Ma'lumot tugadi.")
                    break
                cursor += self.step * batch_limit
                continue

            empty_rounds = 0

            # Oxirgi shamcha hali yopilmagan bo'lishi mumkin â€” uni tashlaymiz
            last_ts = candles[-1][0]
            if last_ts + self.step > now_ms:
                candles = candles[:-1]

            if not candles:
                break

            saved = self.store.save_candles(self.symbol, self.timeframe, candles)
            saved_total += saved

            new_cursor = candles[-1][0] + self.step
            if new_cursor <= cursor:      # oldinga siljimasa â€” to'xtaymiz
                break
            cursor = new_cursor

            progress = min(100, (cursor - start_ms) / (now_ms - start_ms) * 100)
            print(f"   {progress:5.1f}%  |  {ms_to_iso(candles[-1][0])}  "
                  f"|  jami {saved_total:,}", end="\r")

        print(f"\n\nâœ… Yuklandi: {saved_total:,} ta shamcha")
        self.store.log(self.symbol, self.timeframe, "backfill",
                       f"{saved_total} candles, {days} days")
        self.print_stats()

    # â”€â”€ 2. Teshiklarni to'ldirish â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def repair(self):
        gaps = self.store.find_gaps(self.symbol, self.timeframe)

        if not gaps:
            print("âœ… Bo'shliq topilmadi â€” ma'lumot to'liq.")
            return

        print(f"\nðŸ”§ {len(gaps)} ta bo'shliq topildi. To'ldirilmoqda...\n")

        filled = 0
        for i, (gap_start, gap_end) in enumerate(gaps, 1):
            n_missing = (gap_end - gap_start) // self.step + 1
            print(f"   [{i}/{len(gaps)}] {ms_to_iso(gap_start)} â†’ "
                  f"{ms_to_iso(gap_end)}  ({n_missing} ta)")

            cursor = gap_start
            while cursor <= gap_end and self._running:
                try:
                    candles = self.exchange.fetch_ohlcv(
                        self.symbol, timeframe=self.timeframe,
                        since=cursor, limit=1000,
                    )
                except Exception as e:
                    print(f"      âš ï¸  {type(e).__name__}: {e}")
                    time.sleep(3)
                    continue

                if not candles:
                    break

                candles = [c for c in candles if c[0] <= gap_end]
                if not candles:
                    break

                filled += self.store.save_candles(
                    self.symbol, self.timeframe, candles)
                cursor = candles[-1][0] + self.step

        print(f"\nâœ… To'ldirildi: {filled:,} ta shamcha")
        self.store.log(self.symbol, self.timeframe, "repair", f"{filled} candles")

        remaining = self.store.find_gaps(self.symbol, self.timeframe)
        if remaining:
            print(f"âš ï¸  {len(remaining)} ta bo'shliq qoldi "
                  f"(birjada ham yo'q bo'lishi mumkin)")

    # â”€â”€ 3. Jonli oqim â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    async def live(self, poll_seconds: Optional[int] = None):
        """
        Yangi shamchalarni doimiy kuzatadi.

        WebSocket o'rniga polling ishlatamiz â€” soddaroq va
        ishonchliroq. Har shamcha yopilganda bir marta so'rov yuboriladi,
        bu rate limit uchun juda kam.
        """
        interval = poll_seconds or max(5, self.step // 1000 // 10)
        self._stop_event = asyncio.Event()

        print(f"\nðŸ“¡ Jonli rejim: {self.symbol} {self.timeframe}")
        print(f"   Tekshiruv oralig'i: {interval} soniya")
        print("   To'xtatish: Ctrl+C\n")

        consecutive_errors = 0

        async def wait(seconds: float) -> bool:
            """Kutadi, lekin to'xtatish signali kelsa darhol uyg'onadi.
            Qaytaradi: True â€” davom etamiz, False â€” to'xtaymiz."""
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
                return False
            except asyncio.TimeoutError:
                return True

        while self._running:
            try:
                candles = self.exchange.fetch_ohlcv(
                    self.symbol, timeframe=self.timeframe, limit=3)

                if candles:
                    now_ms = int(time.time() * 1000)
                    closed = [c for c in candles if c[0] + self.step <= now_ms]
                    forming = [c for c in candles if c[0] + self.step > now_ms]

                    if closed:
                        n = self.store.save_candles(
                            self.symbol, self.timeframe, closed, closed=True)
                        if n:
                            last = closed[-1]
                            print(f"   âœ“ {ms_to_iso(last[0])}  "
                                  f"O:{last[1]:,.1f}  H:{last[2]:,.1f}  "
                                  f"L:{last[3]:,.1f}  C:{last[4]:,.1f}")

                    if forming:
                        # Shakllanayotgan shamcha â€” closed=0 bilan saqlanadi
                        self.store.save_candles(
                            self.symbol, self.timeframe, forming, closed=False)

                consecutive_errors = 0

            except ccxt.RateLimitExceeded:
                print("   â³ Rate limit â€” kutilmoqda...")
                if not await wait(30):
                    break
                continue

            except Exception as e:
                consecutive_errors += 1
                backoff = min(60, 2 ** consecutive_errors)
                print(f"   âš ï¸  {type(e).__name__}: {e}")
                print(f"      {backoff} soniyadan keyin qayta urinish "
                      f"({consecutive_errors}-marta)")
                self.store.log(self.symbol, self.timeframe, "error", str(e))

                if consecutive_errors >= 10:
                    print("   âŒ 10 marta ketma-ket xato â€” to'xtatilmoqda.")
                    break

                if not await wait(backoff):
                    break
                continue

            if not await wait(interval):
                break

        print("\nðŸ“´ Jonli rejim to'xtadi.")

    # â”€â”€ Statistika â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def print_stats(self):
        s = self.store.stats(self.symbol, self.timeframe)
        print(f"\nðŸ“Š {self.symbol} {self.timeframe}")
        print("   " + "-" * 40)
        for k, v in s.items():
            print(f"   {k:<16} {v}")

    def stop(self):
        self._running = False
        if self._stop_event is not None:
            try:
                loop = asyncio.get_running_loop()
                loop.call_soon_threadsafe(self._stop_event.set)
            except RuntimeError:
                self._stop_event.set()

    def close(self):
        self.store.close()


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# CLI
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Binance ma'lumot yig'uvchi")
    parser.add_argument("mode", choices=["backfill", "repair", "live", "stats"])
    parser.add_argument("--symbol", default=os.getenv("SYMBOL", "BTC/USDT"))
    parser.add_argument("--timeframe", default=os.getenv("TIMEFRAME", "15m"))
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--db", default="data/market.db")
    args = parser.parse_args()

    collector = Collector(args.symbol, args.timeframe, args.db)

    def handle_sigint(sig, frame):
        print("\n\nâ¹  To'xtatilmoqda... (kuting)")
        collector.stop()

    signal.signal(signal.SIGINT, handle_sigint)

    try:
        if args.mode == "backfill":
            collector.backfill(days=args.days)
        elif args.mode == "repair":
            collector.repair()
        elif args.mode == "stats":
            collector.print_stats()
        elif args.mode == "live":
            asyncio.run(collector.live())
    finally:
        collector.close()


if __name__ == "__main__":
    main()
