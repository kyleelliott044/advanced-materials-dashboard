import os
import re
import time
from datetime import date
from typing import Optional
import requests
import psycopg
import yfinance as yf

DATABASE_URL = os.environ["DATABASE_URL"]
BATCH_LIMIT = int(os.environ.get("METRICS_BATCH_LIMIT", "40"))

SEC_HEADERS = {
    "User-Agent": "AdvancedMaterialsResearchPlatform research@materials-macro.io",
    "Accept-Encoding": "gzip, deflate",
}

NON_US_EXCHANGE_SUFFIX_RE = re.compile(
    r"\.(DE|F|PA|AS|BR|SW|L|MI|MC|ST|HE|OL|CO|VI|WA|T|KS|KQ|TW|TWO|HK|SS|SZ|SI|AX|TO|V|SA|MX|JO|TA|SR)$",
    re.IGNORECASE,
)

# Priority-ordered XBRL tags across US-GAAP and IFRS-Full taxonomies
DURATION_TAGS = {
    "revenue": [
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
        ("us-gaap", "RevenueFromContractWithCustomerIncludingAssessedTax"),
        ("us-gaap", "Revenues"),
        ("us-gaap", "SalesRevenueNet"),
        ("ifrs-full", "Revenue"),
    ],
    "gross_profit": [
        ("us-gaap", "GrossProfit"),
        ("ifrs-full", "GrossProfit"),
    ],
    "operating_income": [
        ("us-gaap", "OperatingIncomeLoss"),
        ("ifrs-full", "ProfitLossFromOperatingActivities"),
    ],
    "net_income": [
        ("us-gaap", "NetIncomeLoss"),
        ("us-gaap", "ProfitLoss"),
        ("ifrs-full", "ProfitLossAttributableToOwnersOfParent"),
        ("ifrs-full", "ProfitLoss"),
    ],
    "operating_cash_flow": [
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
        ("ifrs-full", "CashFlowsFromUsedInOperatingActivities"),
    ],
    "capex": [
        ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment"),
        ("us-gaap", "CapitalExpendituresIncurredButNotYetPaid"),
        ("ifrs-full", "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"),
    ],
    "rd_expense": [
        ("us-gaap", "ResearchAndDevelopmentExpense"),
        ("us-gaap", "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"),
        ("ifrs-full", "ResearchAndDevelopmentExpense"),
    ],
}

INSTANT_TAGS = {
    "cash": [
        ("us-gaap", "CashAndCashEquivalentsAtCarryingValue"),
        ("us-gaap", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"),
        ("ifrs-full", "CashAndCashEquivalents"),
    ],
    "long_term_debt": [
        ("us-gaap", "LongTermDebtNoncurrent"),
        ("us-gaap", "LongTermDebt"),
        ("ifrs-full", "NoncurrentPortionOfNoncurrentBorrowings"),
    ],
    "short_term_debt": [
        ("us-gaap", "LongTermDebtCurrent"),
        ("us-gaap", "ShortTermBorrowings"),
        ("us-gaap", "DebtCurrent"),
        ("ifrs-full", "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings"),
    ],
    "inventory": [
        ("us-gaap", "InventoryNet"),
        ("ifrs-full", "Inventories"),
    ],
}


def connect_db():
    return psycopg.connect(
        DATABASE_URL,
        autocommit=True,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5,
    )


def ensure_conn(conn):
    if conn is not None and not conn.closed and not conn.broken:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
    print("Reconnecting to Neon PostgreSQL...", flush=True)
    return connect_db()


def ensure_metrics_table(conn):
    conn = ensure_conn(conn)
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS equity_filing_metrics (
              ticker TEXT PRIMARY KEY REFERENCES equities_universe(ticker) ON DELETE CASCADE,
              source_type TEXT NOT NULL,
              filing_type TEXT NOT NULL,
              fiscal_period TEXT NOT NULL,
              filing_date DATE NOT NULL,
              period_end_date DATE NOT NULL,
              currency TEXT NOT NULL DEFAULT 'USD',
              revenue NUMERIC(20,2),
              revenue_yoy_pct NUMERIC(8,2),
              gross_profit NUMERIC(20,2),
              gross_margin_pct NUMERIC(8,2),
              gross_margin_yoy_bps INTEGER,
              operating_income NUMERIC(20,2),
              operating_margin_pct NUMERIC(8,2),
              net_income NUMERIC(20,2),
              operating_cash_flow NUMERIC(20,2),
              capex NUMERIC(20,2),
              capex_pct_rev NUMERIC(8,2),
              free_cash_flow NUMERIC(20,2),
              rd_expense NUMERIC(20,2),
              rd_pct_rev NUMERIC(8,2),
              inventory NUMERIC(20,2),
              inventory_days INTEGER,
              cash_and_equivalents NUMERIC(20,2),
              total_debt NUMERIC(20,2),
              net_debt NUMERIC(20,2),
              source_url TEXT,
              updated_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)
    conn.commit()
    return conn


def _normalize_co_name(name: str) -> str:
    cleaned = re.sub(
        r"\b(inc|corp|corporation|co|ltd|limited|plc|ag|se|sa|nv|ab|asa|oyj|kgaa|holdings?|group|materials?|technologies|industries)\b",
        "",
        name.lower(),
    )
    return re.sub(r"[^a-z0-9]", "", cleaned)


def fetch_sec_ticker_map():
    resp = requests.get("https://www.sec.gov/files/company_tickers.json", headers=SEC_HEADERS, timeout=20)
    resp.raise_for_status()
    raw = resp.json()
    by_ticker = {}
    by_name = {}
    for entry in raw.values():
        t = entry["ticker"].upper()
        cik_padded = str(entry["cik_str"]).zfill(10)
        pair = (cik_padded, str(entry["cik_str"]))
        by_ticker[t] = pair
        norm = _normalize_co_name(entry.get("title", ""))
        if len(norm) >= 5 and norm not in by_name:
            by_name[norm] = pair
    return by_ticker, by_name


def _lookup_sec_cik(ticker: str, company_name: str, sec_by_ticker: dict, sec_by_name: dict):
    clean_ticker = ticker.upper().strip()
    if clean_ticker in sec_by_ticker:
        return sec_by_ticker[clean_ticker]
    if "." in clean_ticker and NON_US_EXCHANGE_SUFFIX_RE.search(clean_ticker):
        return None
    return sec_by_name.get(_normalize_co_name(company_name))


def _pick_unit_entries(facts_root: dict, taxonomy: str, tag: str) -> tuple[str, list[dict]]:
    tag_obj = facts_root.get(taxonomy, {}).get(tag)
    if not tag_obj:
        return "USD", []
    units = tag_obj.get("units") or {}
    if not units:
        return "USD", []
    if "USD" in units:
        return "USD", units["USD"]
    first_ccy = next(iter(units.keys()))
    return first_ccy, units[first_ccy]


def _duration_days(start_str: Optional[str], end_str: Optional[str]) -> Optional[int]:
    if not start_str or not end_str:
        return None
    try:
        return (date.fromisoformat(end_str[:10]) - date.fromisoformat(start_str[:10])).days
    except ValueError:
        return None


def _matches_duration_bucket(days: Optional[int], bucket: str) -> bool:
    if days is None:
        return False
    if bucket == "annual":
        return 335 <= days <= 395
    if bucket == "quarter":
        return 70 <= days <= 115
    if bucket == "half_year":
        return 160 <= days <= 205
    return False


def _extract_sec_xbrl_metrics(cik_padded: str, cik_unpadded: str) -> Optional[dict]:
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_padded}.json"
    resp = requests.get(url, headers=SEC_HEADERS, timeout=25)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    facts_root = resp.json().get("facts") or {}

    # 1. Collect all candidate FY2025+ revenue facts to anchor the latest filing & period bucket
    rev_candidates = []
    chosen_ccy = "USD"
    for tax, tag in DURATION_TAGS["revenue"]:
        ccy, entries = _pick_unit_entries(facts_root, tax, tag)
        for e in entries:
            form = e.get("form", "")
            if form not in ("10-K", "10-Q", "20-F", "40-F"):
                continue
            end_s = (e.get("end") or "")[:10]
            filed_s = (e.get("filed") or "")[:10]
            if not end_s or end_s < "2025-01-01":
                continue
            days = _duration_days(e.get("start"), end_s)
            bucket = None
            if form in ("10-K", "20-F", "40-F") and _matches_duration_bucket(days, "annual"):
                bucket = "annual"
            elif form in ("10-Q",) and _matches_duration_bucket(days, "quarter"):
                bucket = "quarter"
            elif form in ("20-F", "40-F") and _matches_duration_bucket(days, "half_year"):
                bucket = "half_year"
            if not bucket:
                continue
            val = e.get("val")
            if val is None:
                continue
            rev_candidates.append((filed_s, end_s, form, bucket, e.get("fp", ""), e.get("fy"), e.get("accn", ""), float(val), ccy))

    if not rev_candidates:
        return None

    # Pick the latest filed report (tie-breaking on period_end_date)
    rev_candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    filed_s, end_s, form, bucket, fp, fy, accn, latest_rev, chosen_ccy = rev_candidates[0]
    end_dt = date.fromisoformat(end_s)

    def _get_duration_val(metric_key: str, target_end_dt: date, require_same_accn: bool = True) -> Optional[float]:
        for tax, tag in DURATION_TAGS[metric_key]:
            _, entries = _pick_unit_entries(facts_root, tax, tag)
            matches = []
            for e in entries:
                if e.get("form") not in ("10-K", "10-Q", "20-F", "40-F"):
                    continue
                if require_same_accn and accn and e.get("accn") != accn:
                    continue
                e_end = (e.get("end") or "")[:10]
                if not e_end:
                    continue
                try:
                    dt_diff = abs((date.fromisoformat(e_end) - target_end_dt).days)
                except ValueError:
                    continue
                if dt_diff > 10:
                    continue
                days = _duration_days(e.get("start"), e_end)
                if not _matches_duration_bucket(days, bucket):
                    continue
                if e.get("val") is not None:
                    matches.append(((e.get("filed") or ""), float(e["val"])))
            if matches:
                matches.sort(key=lambda x: x[0], reverse=True)
                return matches[0][1]
        return None

    def _get_instant_val(metric_key: str, target_end_dt: date) -> Optional[float]:
        for tax, tag in INSTANT_TAGS[metric_key]:
            _, entries = _pick_unit_entries(facts_root, tax, tag)
            matches = []
            for e in entries:
                if e.get("form") not in ("10-K", "10-Q", "20-F", "40-F"):
                    continue
                if accn and e.get("accn") != accn:
                    continue
                e_end = (e.get("end") or "")[:10]
                if not e_end:
                    continue
                try:
                    dt_diff = abs((date.fromisoformat(e_end) - target_end_dt).days)
                except ValueError:
                    continue
                if dt_diff <= 10 and e.get("val") is not None:
                    matches.append(((e.get("filed") or ""), float(e["val"])))
            if matches:
                matches.sort(key=lambda x: x[0], reverse=True)
                return matches[0][1]
        return None

    prior_year_dt = date(end_dt.year - 1, end_dt.month, min(end_dt.day, 28))
    prior_rev = _get_duration_val("revenue", prior_year_dt, require_same_accn=False)

    gp = _get_duration_val("gross_profit", end_dt)
    prior_gp = _get_duration_val("gross_profit", prior_year_dt, require_same_accn=False)
    op_inc = _get_duration_val("operating_income", end_dt)
    net_inc = _get_duration_val("net_income", end_dt)
    ocf = _get_duration_val("operating_cash_flow", end_dt)
    capex_raw = _get_duration_val("capex", end_dt)
    capex = abs(capex_raw) if capex_raw is not None else None
    rd = _get_duration_val("rd_expense", end_dt)

    cash = _get_instant_val("cash", end_dt)
    lt_debt = _get_instant_val("long_term_debt", end_dt)
    st_debt = _get_instant_val("short_term_debt", end_dt)
    inventory = _get_instant_val("inventory", end_dt)

    total_debt = None
    if lt_debt is not None or st_debt is not None:
        total_debt = (lt_debt or 0.0) + (st_debt or 0.0)

    period_days = 365 if bucket == "annual" else (182 if bucket == "half_year" else 91)
    fy_lbl = f"FY{fy}" if fy else f"FY{end_dt.year}"
    fp_lbl = "Annual" if bucket == "annual" else (fp or ("H1" if bucket == "half_year" else "Q"))
    fiscal_period = f"{fy_lbl} {form}" if bucket == "annual" else f"{fp_lbl} {fy_lbl} {form}"
    acc_no_dash = accn.replace("-", "")
    source_url = (
        f"https://www.sec.gov/Archives/edgar/data/{cik_unpadded}/{acc_no_dash}"
        if acc_no_dash
        else f"https://www.sec.gov/edgar/browse/?CIK={cik_padded}"
    )

    return _build_metrics_record(
        source_type="SEC XBRL",
        filing_type=form,
        fiscal_period=fiscal_period,
        filing_date=filed_s,
        period_end_date=end_s,
        currency=chosen_ccy,
        revenue=latest_rev,
        prior_revenue=prior_rev,
        gross_profit=gp,
        prior_gross_profit=prior_gp,
        operating_income=op_inc,
        net_income=net_inc,
        operating_cash_flow=ocf,
        capex=capex,
        rd_expense=rd,
        inventory=inventory,
        cash=cash,
        total_debt=total_debt,
        period_days=period_days,
        source_url=source_url,
    )


def _extract_yf_fallback_metrics(ticker: str) -> Optional[dict]:
    """Populates structured filing metrics from Yahoo Finance statements for non-SEC international stocks."""
    try:
        tk = yf.Ticker(ticker)
        info = tk.info or {}
        ccy = info.get("financialCurrency") or info.get("currency") or "USD"
        q_inc = tk.quarterly_income_stmt
        a_inc = tk.income_stmt
        q_bs = tk.quarterly_balance_sheet
        a_bs = tk.balance_sheet
        q_cf = tk.quarterly_cashflow
        a_cf = tk.cashflow
    except Exception:
        return None

    q_cols = sorted(list(q_inc.columns), reverse=True) if (q_inc is not None and not q_inc.empty) else []
    a_cols = sorted(list(a_inc.columns), reverse=True) if (a_inc is not None and not a_inc.empty) else []

    use_quarterly = False
    latest_col = None
    if q_cols and q_cols[0].strftime("%Y-%m-%d") >= "2025-01-01":
        if not a_cols or q_cols[0] >= a_cols[0]:
            use_quarterly = True
            latest_col = q_cols[0]
    if latest_col is None and a_cols and a_cols[0].strftime("%Y-%m-%d") >= "2025-01-01":
        use_quarterly = False
        latest_col = a_cols[0]
    if latest_col is None:
        return None

    inc_df = q_inc if use_quarterly else a_inc
    bs_df = q_bs if (use_quarterly and q_bs is not None and not q_bs.empty) else a_bs
    cf_df = q_cf if (use_quarterly and q_cf is not None and not q_cf.empty) else a_cf
    cols_list = q_cols if use_quarterly else a_cols

    def _val(df, labels, col):
        if df is None or df.empty or col is None or col not in df.columns:
            return None
        for lbl in labels:
            if lbl in df.index:
                v = df.loc[lbl, col]
                if v is not None and v == v:
                    return float(v)
        return None

    prior_col = None
    for c in cols_list[1:]:
        gap = (latest_col - c).days
        if 330 <= gap <= 400:
            prior_col = c
            break

    rev = _val(inc_df, ["Total Revenue", "Operating Revenue"], latest_col)
    if rev is None:
        return None
    prior_rev = _val(inc_df, ["Total Revenue", "Operating Revenue"], prior_col)
    gp = _val(inc_df, ["Gross Profit"], latest_col)
    prior_gp = _val(inc_df, ["Gross Profit"], prior_col)
    op_inc = _val(inc_df, ["Operating Income", "Total Operating Income As Reported"], latest_col)
    net_inc = _val(inc_df, ["Net Income", "Net Income Common Stockholders"], latest_col)
    rd = _val(inc_df, ["Research And Development"], latest_col)

    ocf = _val(cf_df, ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"], latest_col)
    capex_raw = _val(cf_df, ["Capital Expenditure", "Purchase Of PPE"], latest_col)
    capex = abs(capex_raw) if capex_raw is not None else None

    cash = _val(bs_df, ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"], latest_col)
    total_debt = _val(bs_df, ["Total Debt"], latest_col)
    inventory = _val(bs_df, ["Inventory"], latest_col)

    end_s = latest_col.strftime("%Y-%m-%d")
    is_semi = False
    if use_quarterly and len(q_cols) >= 2 and (q_cols[0] - q_cols[1]).days >= 145:
        is_semi = True

    filing_type = "Half-Year Report" if is_semi else ("Quarterly Report" if use_quarterly else "Annual Report")
    period_days = 182 if is_semi else (91 if use_quarterly else 365)

    return _build_metrics_record(
        source_type="Financial Statements",
        filing_type=filing_type,
        fiscal_period=f"{filing_type} ({end_s})",
        filing_date=end_s,
        period_end_date=end_s,
        currency=ccy,
        revenue=rev,
        prior_revenue=prior_rev,
        gross_profit=gp,
        prior_gross_profit=prior_gp,
        operating_income=op_inc,
        net_income=net_inc,
        operating_cash_flow=ocf,
        capex=capex,
        rd_expense=rd,
        inventory=inventory,
        cash=cash,
        total_debt=total_debt,
        period_days=period_days,
        source_url=f"https://finance.yahoo.com/quote/{ticker}/financials",
    )


def _build_metrics_record(
    *,
    source_type: str,
    filing_type: str,
    fiscal_period: str,
    filing_date: str,
    period_end_date: str,
    currency: str,
    revenue: Optional[float],
    prior_revenue: Optional[float],
    gross_profit: Optional[float],
    prior_gross_profit: Optional[float],
    operating_income: Optional[float],
    net_income: Optional[float],
    operating_cash_flow: Optional[float],
    capex: Optional[float],
    rd_expense: Optional[float],
    inventory: Optional[float],
    cash: Optional[float],
    total_debt: Optional[float],
    period_days: int,
    source_url: str,
) -> dict:
    rev_yoy = None
    if revenue is not None and prior_revenue and prior_revenue > 0:
        rev_yoy = round(((revenue - prior_revenue) / prior_revenue) * 100.0, 2)

    gm_pct = round((gross_profit / revenue) * 100.0, 2) if (revenue and gross_profit is not None) else None
    gm_yoy_bps = None
    if gm_pct is not None and prior_revenue and prior_gross_profit is not None and prior_revenue > 0:
        prior_gm = (prior_gross_profit / prior_revenue) * 100.0
        gm_yoy_bps = int(round((gm_pct - prior_gm) * 100))

    om_pct = round((operating_income / revenue) * 100.0, 2) if (revenue and operating_income is not None) else None
    capex_pct = round((capex / revenue) * 100.0, 2) if (revenue and capex is not None) else None
    fcf = round(operating_cash_flow - capex, 2) if (operating_cash_flow is not None and capex is not None) else None
    rd_pct = round((rd_expense / revenue) * 100.0, 2) if (revenue and rd_expense is not None) else None

    inv_days = None
    if inventory is not None and revenue is not None and gross_profit is not None:
        cogs = revenue - gross_profit
        if cogs > 0:
            inv_days = int(round((inventory / cogs) * period_days))

    net_debt = None
    if total_debt is not None or cash is not None:
        net_debt = round((total_debt or 0.0) - (cash or 0.0), 2)

    return {
        "source_type": source_type,
        "filing_type": filing_type,
        "fiscal_period": fiscal_period,
        "filing_date": filing_date,
        "period_end_date": period_end_date,
        "currency": currency,
        "revenue": round(revenue, 2) if revenue is not None else None,
        "revenue_yoy_pct": rev_yoy,
        "gross_profit": round(gross_profit, 2) if gross_profit is not None else None,
        "gross_margin_pct": gm_pct,
        "gross_margin_yoy_bps": gm_yoy_bps,
        "operating_income": round(operating_income, 2) if operating_income is not None else None,
        "operating_margin_pct": om_pct,
        "net_income": round(net_income, 2) if net_income is not None else None,
        "operating_cash_flow": round(operating_cash_flow, 2) if operating_cash_flow is not None else None,
        "capex": round(capex, 2) if capex is not None else None,
        "capex_pct_rev": capex_pct,
        "free_cash_flow": fcf,
        "rd_expense": round(rd_expense, 2) if rd_expense is not None else None,
        "rd_pct_rev": rd_pct,
        "inventory": round(inventory, 2) if inventory is not None else None,
        "inventory_days": inv_days,
        "cash_and_equivalents": round(cash, 2) if cash is not None else None,
        "total_debt": round(total_debt, 2) if total_debt is not None else None,
        "net_debt": net_debt,
        "source_url": source_url,
    }


def run_filing_metrics_extraction(conn=None):
    conn = ensure_metrics_table(conn)
    sec_by_ticker, sec_by_name = fetch_sec_ticker_map()

    with conn.cursor() as cur:
        cur.execute("""
            SELECT e.ticker, e.company_name, e.market_cap_usd
            FROM equities_universe e
            LEFT JOIN equity_filing_metrics m ON e.ticker = m.ticker
            WHERE m.ticker IS NULL
               OR m.updated_at < NOW() - INTERVAL '7 days'
            ORDER BY
                CASE WHEN m.ticker IS NULL THEN 0 ELSE 1 END ASC,
                e.market_cap_usd DESC NULLS LAST,
                e.ticker ASC
            LIMIT %s;
        """, (BATCH_LIMIT,))
        targets = cur.fetchall()

    print(f"Extracting quantitative filing metrics for {len(targets)} companies...", flush=True)
    saved = 0

    for idx, (ticker, company_name, _) in enumerate(targets, start=1):
        cik_pair = _lookup_sec_cik(ticker, company_name, sec_by_ticker, sec_by_name)
        metrics = None
        if cik_pair:
            try:
                metrics = _extract_sec_xbrl_metrics(cik_pair[0], cik_pair[1])
                time.sleep(0.15)
            except Exception as e:
                print(f"  Warning reading SEC XBRL for {ticker}: {e}", flush=True)

        if not metrics:
            metrics = _extract_yf_fallback_metrics(ticker)

        if not metrics:
            print(f"[{idx}/{len(targets)}] {ticker}: No FY2025+ quantitative statements available yet.", flush=True)
            continue

        conn = ensure_conn(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO equity_filing_metrics (
                    ticker, source_type, filing_type, fiscal_period, filing_date, period_end_date,
                    currency, revenue, revenue_yoy_pct, gross_profit, gross_margin_pct, gross_margin_yoy_bps,
                    operating_income, operating_margin_pct, net_income, operating_cash_flow,
                    capex, capex_pct_rev, free_cash_flow, rd_expense, rd_pct_rev,
                    inventory, inventory_days, cash_and_equivalents, total_debt, net_debt,
                    source_url, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, NOW()
                )
                ON CONFLICT (ticker) DO UPDATE SET
                    source_type = EXCLUDED.source_type,
                    filing_type = EXCLUDED.filing_type,
                    fiscal_period = EXCLUDED.fiscal_period,
                    filing_date = EXCLUDED.filing_date,
                    period_end_date = EXCLUDED.period_end_date,
                    currency = EXCLUDED.currency,
                    revenue = EXCLUDED.revenue,
                    revenue_yoy_pct = EXCLUDED.revenue_yoy_pct,
                    gross_profit = EXCLUDED.gross_profit,
                    gross_margin_pct = EXCLUDED.gross_margin_pct,
                    gross_margin_yoy_bps = EXCLUDED.gross_margin_yoy_bps,
                    operating_income = EXCLUDED.operating_income,
                    operating_margin_pct = EXCLUDED.operating_margin_pct,
                    net_income = EXCLUDED.net_income,
                    operating_cash_flow = EXCLUDED.operating_cash_flow,
                    capex = EXCLUDED.capex,
                    capex_pct_rev = EXCLUDED.capex_pct_rev,
                    free_cash_flow = EXCLUDED.free_cash_flow,
                    rd_expense = EXCLUDED.rd_expense,
                    rd_pct_rev = EXCLUDED.rd_pct_rev,
                    inventory = EXCLUDED.inventory,
                    inventory_days = EXCLUDED.inventory_days,
                    cash_and_equivalents = EXCLUDED.cash_and_equivalents,
                    total_debt = EXCLUDED.total_debt,
                    net_debt = EXCLUDED.net_debt,
                    source_url = EXCLUDED.source_url,
                    updated_at = NOW();
                """,
                (
                    ticker,
                    metrics["source_type"],
                    metrics["filing_type"],
                    metrics["fiscal_period"],
                    metrics["filing_date"],
                    metrics["period_end_date"],
                    metrics["currency"],
                    metrics["revenue"],
                    metrics["revenue_yoy_pct"],
                    metrics["gross_profit"],
                    metrics["gross_margin_pct"],
                    metrics["gross_margin_yoy_bps"],
                    metrics["operating_income"],
                    metrics["operating_margin_pct"],
                    metrics["net_income"],
                    metrics["operating_cash_flow"],
                    metrics["capex"],
                    metrics["capex_pct_rev"],
                    metrics["free_cash_flow"],
                    metrics["rd_expense"],
                    metrics["rd_pct_rev"],
                    metrics["inventory"],
                    metrics["inventory_days"],
                    metrics["cash_and_equivalents"],
                    metrics["total_debt"],
                    metrics["net_debt"],
                    metrics["source_url"],
                ),
            )
        conn.commit()
        saved += 1
        print(
            f"[{idx}/{len(targets)}] {ticker}: Saved {metrics['fiscal_period']} ({metrics['source_type']} • Rev={metrics['revenue']} {metrics['currency']})",
            flush=True,
        )

    print(f"Quantitative filing metrics extraction finished ({saved} tickers updated).", flush=True)
    return conn


if __name__ == "__main__":
    conn = connect_db()
    try:
        conn = run_filing_metrics_extraction(conn)
    finally:
        if conn and not conn.closed:
            conn.close()
