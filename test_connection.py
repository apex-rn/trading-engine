"""
test_connection.py — Binance testnet ulanishini tekshirish
===========================================================
Bu skript HECH NARSA SAVDO QILMAYDI.
Faqat ulanishni, balansni va narxni tekshiradi.

Ishga tushirish:
    python test_connection.py
"""

import os
import sys
from dotenv import load_dotenv

try:
    import ccxt
except ImportError:
    print("❌ ccxt o'rnatilmagan. Ishlating: pip install ccxt")
    sys.exit(1)


def main():
    print("=" * 55)
    print("  BINANCE TESTNET ULANISH TESTI")
    print("=" * 55)

    # ── 1. .env faylini o'qish ──────────────────────────────
    load_dotenv()

    api_key = os.getenv("BINANCE_API_KEY", "").strip()
    api_secret = os.getenv("BINANCE_API_SECRET", "").strip()
    testnet = os.getenv("BINANCE_TESTNET", "true").strip().lower() == "true"

    print("\n[1/4] .env fayli tekshirilmoqda...")

    if not api_key or not api_secret:
        print("❌ API kalitlar topilmadi.")
        print("   .env faylini tekshiring:")
        print("   BINANCE_API_KEY=...")
        print("   BINANCE_API_SECRET=...")
        sys.exit(1)

    # Kalitni to'liq ko'rsatmaymiz — faqat boshi
    print(f"✅ API Key topildi: {api_key[:8]}{'.' * 12}")
    print(f"✅ Secret topildi:  {'.' * 20} ({len(api_secret)} belgi)")
    print(f"✅ Rejim: {'TESTNET (soxta pul)' if testnet else '⚠️  REAL PUL'}")

    if not testnet:
        answer = input("\n⚠️  REAL rejim tanlangan! Davom etasizmi? (ha/yo'q): ")
        if answer.strip().lower() not in ("ha", "yes", "y"):
            print("To'xtatildi.")
            sys.exit(0)

    # ── 2. Birjaga ulanish ──────────────────────────────────
    print("\n[2/4] Binance Futures'ga ulanmoqda...")

    exchange = ccxt.binanceusdm({
        "apiKey": api_key,
        "secret": api_secret,
        "enableRateLimit": True,
        "options": {
            "defaultType": "future",
            "adjustForTimeDifference": True,
        },
    })

    if testnet:
        # Binance eski testnet/sandbox rejimini bekor qildi.
        # Endi demo.binance.com ishlatiladi.
        exchange.enable_demo_trading(True)
        print("   → Demo trading rejimi yoqildi (demo.binance.com)")

    try:
        exchange.load_markets()
        print(f"✅ Ulandi. {len(exchange.markets)} ta bozor topildi.")
    except ccxt.AuthenticationError as e:
        print(f"❌ Autentifikatsiya xatosi: {e}")
        print("\n   Sabablari:")
        print("   • API kalit yoki secret noto'g'ri")
        print("   • Kalit testnet'dan emas, real hisobdan olingan")
        print("   • IP cheklovi qo'yilgan, sizning IP'ingiz ro'yxatda yo'q")
        sys.exit(1)
    except ccxt.NetworkError as e:
        print(f"❌ Tarmoq xatosi: {e}")
        print("   Internetni yoki VPN'ni tekshiring.")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Kutilmagan xato: {type(e).__name__}: {e}")
        sys.exit(1)

    # ── 3. Balansni olish ───────────────────────────────────
    print("\n[3/4] Balans olinmoqda...")

    try:
        balance = exchange.fetch_balance()
        usdt = balance.get("USDT", {})

        total = usdt.get("total") or 0
        free = usdt.get("free") or 0
        used = usdt.get("used") or 0

        print(f"✅ USDT balans:")
        print(f"   Jami:      {total:,.2f}")
        print(f"   Bo'sh:     {free:,.2f}")
        print(f"   Band:      {used:,.2f}")

        if total == 0:
            print("\n⚠️  Balans nol. Demo platformada Futures hisobiga")
            print("   soxta pul qo'shish kerak bo'lishi mumkin.")

    except Exception as e:
        print(f"❌ Balans olinmadi: {type(e).__name__}: {e}")
        sys.exit(1)

    # ── 4. Narx va shamchalarni olish ───────────────────────
    symbol = os.getenv("SYMBOL", "BTC/USDT").strip()
    timeframe = os.getenv("TIMEFRAME", "15m").strip()

    print(f"\n[4/4] {symbol} narxi olinmoqda...")

    try:
        ticker = exchange.fetch_ticker(symbol)
        print(f"✅ Hozirgi narx: {ticker['last']:,.2f} USDT")
        print(f"   24s o'zgarish: {ticker.get('percentage', 0):+.2f}%")
        print(f"   24s hajm:      {ticker.get('quoteVolume', 0):,.0f} USDT")

        candles = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=5)
        print(f"\n✅ Oxirgi 5 ta {timeframe} shamcha:")
        print(f"   {'Vaqt':<20} {'Ochilish':>10} {'Yuqori':>10} "
              f"{'Past':>10} {'Yopilish':>10}")
        for c in candles:
            ts = exchange.iso8601(c[0])[:16].replace("T", " ")
            print(f"   {ts:<20} {c[1]:>10,.1f} {c[2]:>10,.1f} "
                  f"{c[3]:>10,.1f} {c[4]:>10,.1f}")

    except ccxt.BadSymbol:
        print(f"❌ '{symbol}' bozori topilmadi.")
        print("   Mavjud bozorlardan namunalar:")
        for m in list(exchange.markets.keys())[:10]:
            print(f"   • {m}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Narx olinmadi: {type(e).__name__}: {e}")
        sys.exit(1)

    # ── Yakun ───────────────────────────────────────────────
    print("\n" + "=" * 55)
    print("  ✅ HAMMASI ISHLAYAPTI")
    print("=" * 55)
    print("\nKeyingi qadam: ma'lumot yig'uvchi moduli.")


if __name__ == "__main__":
    main()
