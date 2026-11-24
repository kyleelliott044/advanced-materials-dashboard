import os
import re
import time
import json
from typing import List, Optional, Literal
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
BATCH_LIMIT = int(os.environ.get("PRODUCT_BATCH_LIMIT", "15"))

SEC_HEADERS = {
    "User-Agent": "AdvancedMaterialsResearchPlatform research@materials-macro.io",
    "Accept-Encoding": "gzip, deflate",
}

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


class RawMaterialSpec(BaseModel):
    name: str = Field(description="Specific chemical, metal, mineral, or feedstock name.")
    commodity_ref: Optional[str] = Field(
        default=None,
        description="Exact input_code from the provided registry list ONLY if an exact match exists; otherwise null. NEVER invent codes.",
    )
    role: str = Field(description="Functional role in the product (e.g., 'abrasive', 'precursor metal', 'resin base').")
    basis: Literal["disclosed", "inferred"]


class AdvancedSpec(BaseModel):
    performance_spec: str = Field(description="Measurable performance parameters (purity, defectivity, thermal stability, dielectric loss, tensile modulus).")
    qualification_months: Optional[int] = Field(default=None, description="Explicitly disclosed customer qualification timeline in months, else null.")
    switching_barrier: str = Field(description="Why switching requires customer process requalification.")


class DownstreamSpec(BaseModel):
    process_step: str = Field(description="Customer manufacturing step where this material is consumed.")
    end_product: str = Field(description="Intermediate component or device built by the customer.")
    end_demand: str = Field(description="Secular end-market demand driver.")
    demand_proxy_ref: Optional[str] = Field(
        default=None,
        description="Exact input_code from the provided demand proxy registry list if applicable, else null.",
    )
    customers: Optional[List[str]] = Field(
        default=None,
        description="Explicitly disclosed customers/OEMs if stated in source text, else null.",
    )


class RevenueSpec(BaseModel):
    basis: str = Field(description="'product' if disclosed at product level, else 'business_unit' or 'company'.")
    share_pct: Optional[float] = Field(default=None, description="Disclosed % of company revenue if explicitly stated, else null.")


class SourceSpec(BaseModel):
    type: Literal["annual_report", "filing", "product_page", "inferred"]
    ref: Optional[str] = Field(default=None, description="Filing form/date or document reference, else null.")


class ProductItem(BaseModel):
    id: str = Field(description="Lowercase slug prefixed with ticker, e.g. 'entg-cmp-slurries'.")
    name: str
    category: str
    business_unit: str
    status: Literal["active", "divested"]
    as_of: str = Field(description="Reporting period, e.g. 'FY2025' or 'Q3 2026'.")
    advanced: AdvancedSpec
    raw_materials: List[RawMaterialSpec]
    downstream: DownstreamSpec
    pricing_structure: Optional[str] = Field(default=None, description="Override only if product contract type differs from company default, else null.")
    revenue: RevenueSpec
    peers: List[str] = Field(default_factory=list, description="Peer tickers from the provided universe list.")
    src: SourceSpec
    conf: Literal["high", "med", "low"]


class ExcludedItem(BaseModel):
    name: str
    reason: str


class CompanyProductsBatch(BaseModel):
    note: Optional[str] = None
    products: List[ProductItem]
    excluded: List[ExcludedItem] = Field(default_factory=list)


def get_registry_and_universe(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (input_code) input_code, input_name, category
            FROM raw_inputs
            ORDER BY input_code, observation_date DESC;
        """)
        registry = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

        cur.execute("SELECT ticker, company_name, sub_sector FROM equities_universe;")
        universe = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

    return registry, universe


def run_product_extraction(conn):
    registry, universe = get_registry_and_universe(conn)
    registry_guide = "\n".join([f"- {code}: {name} ({cat})" for code, (name, cat) in sorted(registry.items())])
    universe_tickers_str = ", ".join(sorted(universe.keys()))

    with conn.cursor() as cur:
        cur.execute("""
            SELECT e.ticker, e.company_name, e.sub_sector, e.market_cap_usd
            FROM equities_universe e
            WHERE NOT EXISTS (SELECT 1 FROM company_products p WHERE p.ticker = e.ticker)
              AND NOT EXISTS (SELECT 1 FROM company_product_profiles pr WHERE pr.ticker = e.ticker)
            ORDER BY e.market_cap_usd DESC NULLS LAST, e.ticker ASC
            LIMIT %s;
        """, (BATCH_LIMIT,))
        targets = cur.fetchall()

    for ticker, company_name, sub_sector, _ in targets:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT filing_type, filing_date::text, headline, executive_summary, pricing_and_margins, capex_and_backlog, source_url
                FROM company_filings_ai
                WHERE ticker = %s
                ORDER BY CASE WHEN filing_type = '10-K' THEN 1 WHEN filing_type = '10-Q' THEN 2 ELSE 3 END, filing_date DESC
                LIMIT 2;
            """, (ticker,))
            filings = cur.fetchall()

        context_lines = [
            f"[{f[0]} {f[1]}] {f[2]} | Summary: {f[3]} | Margins: {f[4] or 'N/A'} | Capex: {f[5] or 'N/A'}"
            for f in filings
        ]
        if not context_lines:
            biz = (yf.Ticker(ticker).info or {}).get("longBusinessSummary", "")[:3000]
            context_lines.append(f"Business Summary: {biz}")

        src_type = "filing" if filings and filings[0][0] in ("10-K", "10-Q") else "inferred"
        src_ref = f"{filings[0][0]} ({filings[0][1]})" if filings and src_type == "filing" else None

        prompt = f"""
        Decompose {company_name} ({ticker}) | Sub-sector: {sub_sector} into advanced-materials products.

        STRICT INCLUSION GATE (ALL 4 MUST HOLD, OTHERWISE PUT IN excluded[]):
        1. Performance-specified (not commodity grade-specified).
        2. Qualified inside a customer's manufacturing process.
        3. Switching requires customer requalification.
        4. Sold as a material input to a downstream manufacturer (exclude equipment, metrology, services, and non-material divisions).

        RULES:
        - Never invent facts, customer names, qualification months, or product revenue shares. Unknown = null.
        - commodity_ref and demand_proxy_ref MUST be an exact input_code from this registry list, or null if not listed:
        {registry_guide}
        - peers MUST only contain valid tickers from: {universe_tickers_str}
        - Set src={{type: "{src_type}", ref: {json.dumps(src_ref)}}}.

        SOURCE CONTEXT:
        {chr(10).join(context_lines)}
        """

        if not client:
            print("GEMINI_API_KEY required for structured extraction.")
            return

        resp = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=CompanyProductsBatch,
                temperature=0.1,
            ),
        )
        parsed = json.loads(resp.text)

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO company_product_profiles (ticker, note)
                VALUES (%s, %s)
                ON CONFLICT (ticker) DO UPDATE SET note = EXCLUDED.note, updated_at = NOW();
                """,
                (ticker, parsed.get("note")),
            )

            for prod in parsed.get("products", []):
                # Enforce valid registry IDs and valid peer tickers
                cleaned_rms = []
                for rm in prod.get("raw_materials", []):
                    cref = rm.get("commodity_ref")
                    if cref not in registry:
                        cref = None
                    cleaned_rms.append({
                        "name": rm["name"],
                        "commodity_ref": cref,
                        "role": rm["role"],
                        "basis": rm["basis"],
                    })

                ds = prod["downstream"]
                dref = ds.get("demand_proxy_ref")
                if dref not in registry:
                    dref = None
                ds["demand_proxy_ref"] = dref

                valid_peers = [p for p in prod.get("peers", []) if p in universe and p != ticker]

                cur.execute(
                    """
                    INSERT INTO company_products (
                        id, ticker, name, category, business_unit, status, as_of,
                        advanced, raw_materials, downstream, pricing_structure,
                        revenue, peers, src, conf
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s::jsonb, %s, %s::jsonb, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name,
                        category = EXCLUDED.category,
                        business_unit = EXCLUDED.business_unit,
                        status = EXCLUDED.status,
                        as_of = EXCLUDED.as_of,
                        advanced = EXCLUDED.advanced,
                        raw_materials = EXCLUDED.raw_materials,
                        downstream = EXCLUDED.downstream,
                        pricing_structure = EXCLUDED.pricing_structure,
                        revenue = EXCLUDED.revenue,
                        peers = EXCLUDED.peers,
                        src = EXCLUDED.src,
                        conf = EXCLUDED.conf,
                        updated_at = NOW();
                    """,
                    (
                        prod["id"],
                        ticker,
                        prod["name"],
                        prod["category"],
                        prod["business_unit"],
                        prod["status"],
                        prod["as_of"],
                        json.dumps(prod["advanced"]),
                        json.dumps(cleaned_rms),
                        json.dumps(ds),
                        prod.get("pricing_structure"),
                        json.dumps(prod["revenue"]),
                        valid_peers,
                        json.dumps(prod["src"]),
                        prod["conf"],
                    ),
                )

            for ex in parsed.get("excluded", []):
                cur.execute(
                    """
                    INSERT INTO company_excluded_products (ticker, name, reason)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (ticker, name) DO UPDATE SET reason = EXCLUDED.reason, updated_at = NOW();
                    """,
                    (ticker, ex["name"], ex["reason"]),
                )
        conn.commit()
        print(f"[{ticker}] Saved {len(parsed.get('products', []))} products & {len(parsed.get('excluded', []))} excluded items.")
        time.sleep(0.5)


if __name__ == "__main__":
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        run_product_extraction(conn)
