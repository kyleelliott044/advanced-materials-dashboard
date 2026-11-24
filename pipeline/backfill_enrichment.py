import os
import re
import time
import json
from typing import List, Optional
import requests
import psycopg
import yfinance as yf
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

DATABASE_URL = os.environ["DATABASE_URL"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
BATCH_LIMIT = int(os.environ.get("BATCH_LIMIT", "25"))

SEC_HEADERS = {
    "User-Agent": "AdvancedMaterialsResearchPlatform research@materials-macro.io",
    "Accept-Encoding": "gzip, deflate",
}

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

MAX_GEMINI_CALLS = 18
gemini_calls_used = 0
groq_calls_used = 0
gemini_exhausted = False

GROQ_MODELS = [
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-20b",
]
exhausted_groq_models = set()

CANONICAL_INPUT_GUIDE = """
- HG=F: Copper
- NG=F: Natural Gas
- CL=F: WTI Crude Oil
- BZ=F: Brent Crude Oil
- ALI=F: Aluminum
- PL=F: Platinum
- SI=F: Silver
- PNICKUSDM: Nickel
- PCOBAUSDM: Cobalt
- PNGASEUUSDM: European Natural Gas
- WPU101: Iron & Steel
- WPU061: Industrial Chemicals
- WPU066: Plastic Resins & Polymers
- NDPR_CN: Neodymium-Praseodymium (NdPr) Rare Earths
- LICARB_CN: Lithium Carbonate / Hydroxide
- GALLIUM_9999: Gallium
- GERMANIUM_INGOT: Germanium
- POLYSILICON_N: Polysilicon / Silicon Wafers
- SPHERICAL_GRAPHITE: Graphite (Natural / Synthetic)
- COBALT_SULFATE: Cobalt Sulfate
- CARBON_FIBER_T700: Carbon Fiber / PAN Precursor
- SMH: Semiconductor Wafer Fab Demand
- ITA: Aerospace & Defense Build Rates
- LIT: Lithium & EV Battery Supply Chain
- REMX: Rare Earth & Strategic Metals
- URA: Uranium & Nuclear Fuel
- SLX: Specialty & Stainless Steel
- IHI: Medical Devices & Biomaterials
- INDPRO: US Industrial Production
- DGS10: 10-Year Treasury Yield / Cost of Capital
"""


class CommodityDriverLink(BaseModel):
    commodity_name: str = Field(
        description="Human-readable name of the specific commodity, feedstock, energy input, or end-market driver (e.g., 'High-Purity Nickel', 'Natural Gas', 'Lithium Carbonate', 'Titanium Sponge', 'Copper Foil'). Never return a ticker symbol or FRED code."
    )
    matched_input_code: Optional[str] = Field(
        default=None,
        description="Closest matching code from the canonical input list if applicable (e.g., 'PNICKUSDM', 'NG=F', 'HG=F', 'LICARB_CN', 'SMH', 'ITA'), otherwise null.",
    )
    exposure_role: str = Field(
        description="Must be one of: 'Cost Input', 'Revenue Driver', 'Margin Spread', or 'Byproduct Credit'."
    )
    impact_direction: str = Field(
        description="Must be one of: 'Pass-Through', 'Inverse (Cost)', 'Direct (Demand)', or 'Hedged / Fixed'."
    )
    pass_through_lag: str = Field(
        description="Pricing adjustment speed, e.g., 'Monthly Index', '1-2 Quarter Lag', 'Multi-Year LTA', 'Spot / Unhedged', or 'Take-or-Pay'."
    )
    cogs_or_rev_share_pct: Optional[float] = Field(
        default=None,
        description="Disclosed % of COGS or revenue if explicitly stated, otherwise null.",
    )
    mechanism_detail: str = Field(
        description="2-sentence institutional explanation of how this commodity impacts gross margins, surcharges, inventory valuation, or revenue."
    )
    source_quote: str = Field(
        description="Short verbatim phrase from the filing text referencing this raw material, energy input, or commodity driver."
    )


class SupplyChainLink(BaseModel):
    counterparty_name: str = Field(
        description="Exact company/organization name if explicitly disclosed (e.g., 'Apple', 'TSMC', 'Boeing', 'Airbus', 'U.S. Department of Defense'), OR named counterparty group if anonymized (e.g., 'Customer A', 'Tier-1 Aerospace OEMs')."
    )
    is_named_entity: bool = Field(
        description="True ONLY if a specific company or organization name is explicitly stated in the text."
    )
    material_or_product: str = Field(
        description="Specific raw material/feedstock purchased (for suppliers), overlapping product/segment (for competitors), or engineered product/material sold (for customers)."
    )
    is_sole_source: bool = Field(
        description="True if text notes single-source, sole-supplier, limited-source, primary/direct rival, or 10%+ customer concentration."
    )
    exposure_pct: Optional[float] = Field(
        default=None,
        description="Disclosed % of revenue, raw material spend, or market share if stated, otherwise null.",
    )
    source_quote: str = Field(
        description="Verbatim 10-30 word quote from the source text proving this supplier, competitor, or customer relationship."
    )


class EnrichmentBackfillSchema(BaseModel):
    commodity_drivers: List[CommodityDriverLink] = Field(
        description="3 to 6 specific company-level commodity, raw material, feedstock, and energy drivers."
    )
    upstream_suppliers: List[SupplyChainLink] = Field(
        default_factory=list,
        description="Disclosed upstream raw material suppliers, feedstock sources, or sole-source/limited-source inputs.",
    )
    competitors: List[SupplyChainLink] = Field(
        default_factory=list,
        description="Explicitly named competitors or rival manufacturers disclosed in Item 1 Competition or MD&A.",
    )
    downstream_customers: List[SupplyChainLink] = Field(
        default_factory=list,
        description="Disclosed downstream customers, OEMs, or major customer concentration disclosures.",
    )


def ensure_tables(conn):
    with conn.cursor() as cur:
        cur.execute("""
            ALTER TABLE equities_universe
              ADD COLUMN IF NOT EXISTS enrichment_backfilled_at TIMESTAMPTZ;

            CREATE TABLE IF NOT EXISTS equity_commodity_drivers (
              id SERIAL PRIMARY KEY,
              ticker TEXT NOT NULL REFERENCES equities_universe(ticker) ON DELETE CASCADE,
              commodity_name TEXT NOT NULL,
              matched_input_code TEXT,
              exposure_role TEXT NOT NULL,
              impact_direction TEXT NOT NULL,
              pass_through_lag TEXT,
              cogs_or_rev_share_pct NUMERIC(5,2),
              mechanism_detail TEXT NOT NULL,
              source_filing_type TEXT,
              source_filing_date DATE,
              source_quote TEXT,
              updated_at TIMESTAMPTZ DEFAULT NOW(),
              UNIQUE (ticker, commodity_name)
            );

            CREATE TABLE IF NOT EXISTS equity_supply_chain (
              id SERIAL PRIMARY KEY,
              ticker TEXT NOT NULL REFERENCES equities_universe(ticker) ON DELETE CASCADE,
              direction TEXT NOT NULL,
              counterparty_name TEXT NOT NULL,
              is_named_entity BOOLEAN DEFAULT TRUE,
              material_or_product TEXT NOT NULL,
              is_sole_source BOOLEAN DEFAULT FALSE,
              exposure_pct NUMERIC(5,2),
              source_filing_type TEXT,
              source_filing_date DATE,
              source_quote TEXT,
              updated_at TIMESTAMPTZ DEFAULT NOW(),
              UNIQUE (ticker, direction, counterparty_name, material_or_product)
            );

            ALTER TABLE equity_supply_chain
              DROP CONSTRAINT IF EXISTS equity_supply_chain_direction_check;

            ALTER TABLE equity_supply_chain
              ADD CONSTRAINT equity_supply_chain_direction_check
              CHECK (direction IN ('upstream_supplier', 'downstream_customer', 'competitor'));
        """)
    conn.commit()


def _safe_pct(val) -> Optional[float]:
    if val is None:
        return None
    try:
        num = float(val)
        if 0 <= num <= 100:
            return round(num, 2)
    except (TypeError, ValueError):
        pass
    return None


def _call_ai(prompt: str, schema) -> Optional[dict]:
    global gemini_calls_used, groq_calls_used, gemini_exhausted

    if client and not gemini_exhausted and gemini_calls_used < MAX_GEMINI_CALLS:
        for attempt in range(2):
            try:
                gemini_calls_used += 1
                resp = client.models.generate_content(
                    model="gemini-3.8-flash",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=schema,
                        temperature=0.15,
                    ),
                )
                return json.loads(resp.text)
            except Exception as e:
                err = str(e)
                if "429" in err or "RESOURCE_EXHAUSTED" in err:
                    gemini_exhausted = True
                    print("Gemini daily quota reached — switching to Groq cascade.")
                    break
                if any(k in err for k in ("503", "UNAVAILABLE", "disconnected", "timed out")) and attempt == 0:
                    time.sleep(4)
                    continue
                print(f"Gemini error ({e}) — switching to Groq cascade.")
                break

    if not GROQ_API_KEY:
        return None

    schema_json = json.dumps(schema.model_json_schema())
    trimmed_prompt = prompt[:16000]

    for model_id in GROQ_MODELS:
        if model_id in exhausted_groq_models:
            continue

        for attempt in range(2):
            try:
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {GROQ_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model_id,
                        "response_format": {"type": "json_object"},
                        "temperature": 0.15,
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "You are an institutional advanced materials analyst. "
                                    "Return ONLY a valid JSON object matching this exact schema:\n"
                                    f"{schema_json}"
                                ),
                            },
                            {"role": "user", "content": trimmed_prompt},
                        ],
                    },
                    timeout=45,
                )
            except requests.RequestException:
                break

            if resp.status_code == 429:
                err_lower = resp.text.lower()
                if "tokens per day" in err_lower or "requests per day" in err_lower:
                    print(f"Groq daily limit reached for {model_id} — switching model.")
                    exhausted_groq_models.add(model_id)
                    break
                if attempt == 0:
                    print(f"Groq TPM limit on {model_id} — waiting 15s...")
                    time.sleep(15)
                    continue
                break

            if resp.status_code in (400, 404, 413):
                if resp.status_code == 404:
                    exhausted_groq_models.add(model_id)
                break

            resp.raise_for_status()
            groq_calls_used += 1
            time.sleep(3)
            return json.loads(resp.json()["choices"][0]["message"]["content"])

    return None


def _extract_keyword_windows(text: str, patterns: list[str], window: int = 1600, max_total: int = 8000) -> str:
    chunks = []
    used_ranges = []
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            start = max(0, m.start() - 350)
            end = min(len(text), m.end() + window)
            if any(not (end < s or start > e) for s, e in used_ranges):
                continue
            used_ranges.append((start, end))
            chunks.append(text[start:end])
            if sum(len(c) for c in chunks) >= max_total:
                return " ... ".join(chunks)
    return " ... ".join(chunks)


def fetch_sec_targeted_excerpt(source_url: str) -> str:
    resp = requests.get(source_url, headers=SEC_HEADERS, timeout=25)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    text = re.sub(r"\s+", " ", soup.get_text(separator=" ", strip=True))

    # Targeted supply-chain, competition, and customer-concentration windows (kept early so Groq sees them)
    sc_windows = _extract_keyword_windows(
        text,
        [
            r"(raw materials|sources and availability of raw materials|sole[- ]source|single[- ]source|limited[- ]source|principal suppliers)",
            r"(major customers|customer concentration|accounted for \d+% of (consolidated |total )?(net )?(sales|revenue))",
            r"(competition|competitive conditions|principal competitors|major competitors|compete directly with)",
        ],
        window=1600,
        max_total=8000,
    )

    # Skip Table of Contents by taking the last match of Item 1. Business and MD&A
    item1_matches = list(re.finditer(r"Item\s+1\.\s+Business", text, re.IGNORECASE))
    mda_matches = list(re.finditer(r"(Item\s+2\.|Item\s+7\.)\s+Management.{0,30}Discussion", text, re.IGNORECASE))

    item1_slice = text[item1_matches[-1].start() : item1_matches[-1].start() + 6500] if item1_matches else ""
    mda_slice = (
        text[mda_matches[-1].start() : mda_matches[-1].start() + 11000]
        if mda_matches
        else text[5000:16000]
    )

    combined = f"{sc_windows} ... {item1_slice} ... {mda_slice}".strip(" .")
    return combined[:25000]


def _quote_exists(quote: str, source_text: str) -> bool:
    if not quote or len(quote.strip()) < 10:
        return False
    clean_q = re.sub(r"[^a-z0-9 ]+", " ", quote.lower()).split()
    clean_s = " ".join(re.sub(r"[^a-z0-9 ]+", " ", source_text.lower()).split())
    if len(clean_q) < 4:
        return " ".join(clean_q) in clean_s
    return any(" ".join(clean_q[i : i + 4]) in clean_s for i in range(len(clean_q) - 3))


def save_enrichment(conn, ticker: str, ftype: str, fdate: str, parsed: dict, source_text: str):
    driver_rows = []
    for d in (parsed.get("commodity_drivers") or []):
        cname = (d.get("commodity_name") or "").strip()
        mech = (d.get("mechanism_detail") or "").strip()
        if not cname or not mech:
            continue
        code = (d.get("matched_input_code") or "").strip() or None
        driver_rows.append((
            ticker,
            cname[:100],
            code[:32] if code else None,
            (d.get("exposure_role") or "Cost Input")[:50],
            (d.get("impact_direction") or "Pass-Through")[:50],
            (d.get("pass_through_lag") or "Quarterly")[:60],
            _safe_pct(d.get("cogs_or_rev_share_pct")),
            mech[:600],
            ftype,
            fdate,
            (d.get("source_quote") or "")[:350],
        ))

    if driver_rows:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO equity_commodity_drivers (
                    ticker, commodity_name, matched_input_code, exposure_role,
                    impact_direction, pass_through_lag, cogs_or_rev_share_pct,
                    mechanism_detail, source_filing_type, source_filing_date, source_quote
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker, commodity_name) DO UPDATE SET
                    matched_input_code = COALESCE(EXCLUDED.matched_input_code, equity_commodity_drivers.matched_input_code),
                    exposure_role = EXCLUDED.exposure_role,
                    impact_direction = EXCLUDED.impact_direction,
                    pass_through_lag = EXCLUDED.pass_through_lag,
                    cogs_or_rev_share_pct = COALESCE(EXCLUDED.cogs_or_rev_share_pct, equity_commodity_drivers.cogs_or_rev_share_pct),
                    mechanism_detail = EXCLUDED.mechanism_detail,
                    source_filing_type = EXCLUDED.source_filing_type,
                    source_filing_date = EXCLUDED.source_filing_date,
                    source_quote = EXCLUDED.source_quote,
                    updated_at = NOW();
                """,
                driver_rows,
            )

    sc_rows = []
    for direction, key in (
        ("upstream_supplier", "upstream_suppliers"),
        ("competitor", "competitors"),
        ("downstream_customer", "downstream_customers"),
    ):
        for item in (parsed.get(key) or []):
            cname = (item.get("counterparty_name") or "").strip()
            mat = (item.get("material_or_product") or "").strip()
            if not cname or not mat:
                continue
            quote = (item.get("source_quote") or "").strip()
            if item.get("is_named_entity") and not _quote_exists(quote, source_text):
                continue
            sc_rows.append((
                ticker,
                direction,
                cname[:140],
                bool(item.get("is_named_entity", False)),
                mat[:180],
                bool(item.get("is_sole_source", False)),
                _safe_pct(item.get("exposure_pct")),
                ftype,
                fdate,
                quote[:400],
            ))

    if sc_rows:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO equity_supply_chain (
                    ticker, direction, counterparty_name, is_named_entity,
                    material_or_product, is_sole_source, exposure_pct,
                    source_filing_type, source_filing_date, source_quote
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker, direction, counterparty_name, material_or_product) DO UPDATE SET
                    is_named_entity = EXCLUDED.is_named_entity,
                    is_sole_source = EXCLUDED.is_sole_source,
                    exposure_pct = COALESCE(EXCLUDED.exposure_pct, equity_supply_chain.exposure_pct),
                    source_filing_type = EXCLUDED.source_filing_type,
                    source_filing_date = EXCLUDED.source_filing_date,
                    source_quote = EXCLUDED.source_quote,
                    updated_at = NOW();
                """,
                sc_rows,
            )

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE equities_universe SET enrichment_backfilled_at = NOW() WHERE ticker = %s;",
            (ticker,),
        )

    conn.commit()
    print(f"[{ticker}] Saved {len(driver_rows)} commodity drivers & {len(sc_rows)} value-chain/competitor links ({ftype} {fdate})")


def run_backfill(conn):
    ensure_tables(conn)

    # Select best already-parsed filing per ticker where enrichment has not yet been run
    with conn.cursor() as cur:
        cur.execute("""
            WITH best_filing AS (
                SELECT DISTINCT ON (f.ticker)
                    f.ticker,
                    e.company_name,
                    e.sub_sector,
                    e.contract_type,
                    e.qualification_years,
                    e.enrichment_backfilled_at,
                    f.filing_type,
                    f.filing_date::text AS filing_date,
                    f.executive_summary,
                    f.pricing_and_margins,
                    f.capex_and_backlog,
                    f.key_risks,
                    f.source_url
                FROM company_filings_ai f
                JOIN equities_universe e ON f.ticker = e.ticker
                ORDER BY
                    f.ticker,
                    CASE
                        WHEN f.filing_type = '10-K' THEN 1
                        WHEN f.filing_type = '10-Q' THEN 2
                        ELSE 3
                    END ASC,
                    f.filing_date DESC
            )
            SELECT
                bf.ticker,
                bf.company_name,
                bf.sub_sector,
                bf.contract_type,
                bf.qualification_years,
                bf.filing_type,
                bf.filing_date,
                bf.executive_summary,
                bf.pricing_and_margins,
                bf.capex_and_backlog,
                bf.key_risks,
                bf.source_url
            FROM best_filing bf
            WHERE bf.enrichment_backfilled_at IS NULL
              AND (
                  NOT EXISTS (SELECT 1 FROM equity_commodity_drivers d WHERE d.ticker = bf.ticker)
                  OR NOT EXISTS (SELECT 1 FROM equity_supply_chain sc WHERE sc.ticker = bf.ticker AND sc.direction = 'competitor')
              )
            ORDER BY
                CASE WHEN bf.source_url LIKE '%%sec.gov%%' THEN 0 ELSE 1 END ASC,
                bf.qualification_years DESC NULLS LAST,
                bf.ticker ASC
            LIMIT %s;
        """, (BATCH_LIMIT,))
        targets = cur.fetchall()

    if not targets:
        print("All already-analyzed filings have been processed for commodity drivers, competitors, and supply-chain links.")
        return

    print(f"Found {len(targets)} analyzed stocks to enrich (batch limit={BATCH_LIMIT})...")

    for (
        ticker,
        company_name,
        sub_sector,
        contract_type,
        _,
        ftype,
        fdate,
        exec_summary,
        pricing_margins,
        capex_backlog,
        key_risks,
        source_url,
    ) in targets:
        stored_context = (
            f"Summary: {exec_summary or ''}\n"
            f"Pricing & Margins: {pricing_margins or ''}\n"
            f"Capex & Backlog: {capex_backlog or ''}\n"
            f"Risks: {key_risks or ''}"
        )

        filing_excerpt = ""
        if source_url and "sec.gov" in source_url:
            try:
                filing_excerpt = fetch_sec_targeted_excerpt(source_url)
                time.sleep(0.2)
            except Exception as e:
                print(f"Warning fetching SEC text for {ticker}: {e}")
        else:
            try:
                biz = (yf.Ticker(ticker).info or {}).get("longBusinessSummary", "")
                filing_excerpt = biz[:3500]
            except Exception:
                pass

        full_source_text = f"{filing_excerpt}\n{stored_context}".strip()

        prompt = f"""
        You are an institutional advanced materials equity & supply-chain analyst.
        Analyze the filing text and parsed summary below for {company_name} ({ticker}) | Sub-sector: {sub_sector} | Contract Structure: {contract_type} | Source: {ftype} ({fdate}).

        Extract TWO structured sets of intelligence:
        1. commodity_drivers: 3 to 6 specific raw materials, feedstocks, energy inputs, and commodity benchmarks that drive this company's costs, margins, or revenues. Use clean human-readable names (never ticker symbols or FRED codes) and map each to the closest canonical code when applicable:
        {CANONICAL_INPUT_GUIDE}

        2. upstream_suppliers, competitors & downstream_customers:
           - upstream_suppliers: Explicitly disclosed raw material suppliers, feedstock sources, or sole-source/single-source inputs.
           - competitors: Explicitly named rival companies disclosed in the Competition section or MD&A (noting overlapping product lines and any disclosed market share %).
           - downstream_customers: Explicitly disclosed downstream customers, OEMs, or 10%+ customer concentration disclosures.
           Back every named entity with a verbatim quote from the source text. Do NOT invent company names not present in the text.

        SOURCE TEXT:
        {full_source_text}
        """

        parsed = _call_ai(prompt, EnrichmentBackfillSchema)
        if not parsed:
            print("All AI providers exhausted for this run — stopping cleanly.")
            break

        save_enrichment(conn, ticker, ftype, fdate, parsed, full_source_text)

    print(f"Backfill run complete ({gemini_calls_used} Gemini calls, {groq_calls_used} Groq calls used).")


if __name__ == "__main__":
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        run_backfill(conn)
