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


def update_market_caps(conn):
    fx_rates = fetch_fx_to_usd()
    with conn.cursor() as cur:
        cur.execute("SELECT ticker FROM equities_universe ORDER BY ticker;")
        tickers = [r[0] for r in cur.fetchall()]

    updates = []
    for ticker in tickers:
        try:
            fi = yf.Ticker(ticker).fast_info
            raw_cap = getattr(fi, "market_cap", None)
            ccy = getattr(fi, "currency", None) or "USD"
            if not raw_cap or raw_cap <= 0:
                continue

            # London Stock Exchange quotes often use British pence (GBp = 0.01 GBP)
            if ccy == "GBp":
                fx = fx_rates.get("GBP", 1.27) / 100.0
            else:
                fx = fx_rates.get(ccy.upper())

            if not fx:
                print(f"Skipping {ticker}: missing FX rate for {ccy}")
                continue

            usd_cap = int(round(float(raw_cap) * fx))
            updates.append((usd_cap, ticker))
            time.sleep(0.05)
        except Exception as e:
            print(f"Warning fetching market cap for {ticker}: {e}")

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
