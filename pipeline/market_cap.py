import os
import time
import psycopg
import yfinance as yf

DATABASE_URL = os.environ["DATABASE_URL"]

FX_PAIRS = {
    "EUR": "EURUSD=X",
    "JPY": "JPYUSD=X",
    "KRW": "KRWUSD=X",
    "TWD": "TWDUSD=X",
    "CNY": "CNYUSD=X",
    "CNH": "CNYUSD=X",
    "HKD": "HKDUSD=X",
    "GBP": "GBPUSD=X",
    "CHF": "CHFUSD=X",
    "AUD": "AUDUSD=X",
    "CAD": "CADUSD=X",
    "SEK": "SEKUSD=X",
    "NOK": "NOKUSD=X",
    "INR": "INRUSD=X",
}

DEPRECATED_REPLACEMENTS = {
    "066970.KQ": "066970.KS",
    "500380.NS": "SRF.NS",
    "8070.TWO": "8070.TW",
    "MRSN.PA": "MRN.PA",
    "4103.T": "4109.T",
    "QNTY": "Q",
}


def cleanup_duplicate_tickers(conn):
    """Removes re-seeded legacy tickers when their updated replacement ticker already exists."""
    with conn.cursor() as cur:
        for old_tk, new_tk in DEPRECATED_REPLACEMENTS.items():
            cur.execute("SELECT 1 FROM equities_universe WHERE ticker = %s;", (new_tk,))
            if cur.fetchone():
                cur.execute(
                    """
                    DELETE FROM equities_universe
                    WHERE ticker = %s
                      AND NOT EXISTS (SELECT 1 FROM company_filings_ai WHERE ticker = %s);
                    """,
                    (old_tk, old_tk),
                )
    conn.commit()


def fetch_fx_to_usd() -> dict[str, float]:
    rates = {"USD": 1.0}
    tickers = list(set(FX_PAIRS.values()))
    df = yf.download(tickers, period="5d", interval="1d", progress=False)["Close"]
    for ccy, pair in FX_PAIRS.items():
        try:
            series = df[pair].dropna() if pair in df.columns else df.dropna()
            if not series.empty:
                rates[ccy] = float(series.iloc[-1])
        except Exception:
            pass
    return rates


def _get_cap_and_currency(ticker: str):
    tk = yf.Ticker(ticker)
    try:
        fi = tk.fast_info
        raw_cap = fi["market_cap"]
        ccy = fi["currency"] or "USD"
        if raw_cap and raw_cap > 0:
            return float(raw_cap), str(ccy)
    except Exception:
        pass

    try:
        info = tk.info or {}
        raw_cap = info.get("marketCap")
        ccy = info.get("currency") or "USD"
        if raw_cap and raw_cap > 0:
            return float(raw_cap), str(ccy)
    except Exception:
        pass

    return None, None


def update_market_caps(conn):
    cleanup_duplicate_tickers(conn)
    fx_rates = fetch_fx_to_usd()

    with conn.cursor() as cur:
        cur.execute("SELECT ticker FROM equities_universe ORDER BY ticker;")
        tickers = [r[0] for r in cur.fetchall()]

    updates = []
    for ticker in tickers:
        active_ticker = ticker
        raw_cap, ccy = _get_cap_and_currency(active_ticker)

        # Auto-test KOSDAQ (.KQ) if KOSPI (.KS) returns 404
        if (not raw_cap or not ccy) and active_ticker.endswith(".KS"):
            alt_ticker = active_ticker[:-3] + ".KQ"
            alt_cap, alt_ccy = _get_cap_and_currency(alt_ticker)
            if alt_cap and alt_ccy:
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE equities_universe SET ticker = %s WHERE ticker = %s;",
                            (alt_ticker, active_ticker),
                        )
                    conn.commit()
                    active_ticker, raw_cap, ccy = alt_ticker, alt_cap, alt_ccy
                    print(f"Migrated {ticker} -> {alt_ticker}")
                except Exception:
                    conn.rollback()

        if not raw_cap or not ccy:
            continue

        if ccy == "GBp":
            fx = fx_rates.get("GBP", 1.27) / 100.0
        else:
            fx = fx_rates.get(ccy.upper())

        if not fx:
            print(f"Skipping {active_ticker}: missing FX rate for {ccy}")
            continue

        usd_cap = int(round(raw_cap * fx))
        updates.append((usd_cap, active_ticker))
        time.sleep(0.05)

    with conn.cursor() as cur:
        cur.executemany(
            """
            UPDATE equities_universe
            SET market_cap_usd = %s,
                market_cap_updated_at = NOW()
            WHERE ticker = %s;
            """,
            updates,
        )
    conn.commit()
    print(f"Updated USD market cap for {len(updates)}/{len(tickers)} equities.")


if __name__ == "__main__":
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        update_market_caps(conn)
