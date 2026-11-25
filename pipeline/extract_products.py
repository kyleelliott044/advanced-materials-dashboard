import os
import time
import json
from typing import List, Optional, Literal
import requests
import psycopg
import yfinance as yf
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from ai_usage_logger import record_ai_usage

DATABASE_URL = os.environ["DATABASE_URL"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
BATCH_LIMIT = int(os.environ.get("PRODUCT_BATCH_LIMIT", "15"))

def _build_gemini_client():
    if not GEMINI_API_KEY:
        return None
    try:
        return genai.Client(
            api_key=GEMINI_API_KEY,
            http_options=types.HttpOptions(timeout=45000),
        )
    except TypeError:
        return genai.Client(api_key=GEMINI_API_KEY)


client = _build_gemini_client()

MAX_GEMINI_CALLS = 18
gemini_calls_used = 0
groq_calls_used = 0
gemini_unavailable = False

GROQ_MODELS = [
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-20b",
]
exhausted_groq_models = set()


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


def _call_ai(prompt: str, schema) -> Optional[dict]:
    global gemini_calls_used, groq_calls_used, gemini_unavailable

    if client and not gemini_unavailable and gemini_calls_used < MAX_GEMINI_CALLS:
        try:
            gemini_calls_used += 1
            resp = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=0.1,
                ),
            )
            return json.loads(resp.text)
        except Exception as e:
            gemini_unavailable = True
            print(f"Gemini unavailable/quota ({e}) — switching immediately to Groq cascade.", flush=True)

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
                        "temperature": 0.1,
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
            except requests.RequestException as e:
                print(f"Groq network error on {model_id}: {e}", flush=True)
                break

            if resp.status_code == 429:
                err_lower = resp.text.lower()
                if "tokens per day" in err_lower or "requests per day" in err_lower:
                    print(f"Groq daily limit reached for {model_id} — switching model.", flush=True)
                    exhausted_groq_models.add(model_id)
                    break
                if attempt == 0:
                    print(f"Groq TPM limit on {model_id} — waiting 15s...", flush=True)
                    time.sleep(15)
                    continue
                break

            if resp.status_code in (400, 404, 413):
                print(f"Groq {resp.status_code} on {model_id} — switching model.", flush=True)
                if resp.status_code == 404:
                    exhausted_groq_models.add(model_id)
                break

            resp.raise_for_status()
            groq_calls_used += 1
            time.sleep(2)
            return json.loads(resp.json()["choices"][0]["message"]["content"])

    return None


def get_registry_and_universe(conn):
    conn = ensure_conn(conn)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (input_code) input_code, input_name, category
            FROM raw_inputs
            ORDER BY input_code, observation_date DESC;
        """)
        registry = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

        cur.execute("SELECT ticker, company_name, sub_sector FROM equities_universe;")
        universe = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

    return conn, registry, universe


def run_product_extraction(conn):
    conn, registry, universe = get_registry_and_universe(conn)
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

    print(f"Starting product extraction for {len(targets)} companies (BATCH_LIMIT={BATCH_LIMIT})...", flush=True)

    for idx, (ticker, company_name, sub_sector, _) in enumerate(targets, start=1):
        print(f"[{idx}/{len(targets)}] Extracting {ticker} ({company_name})...", flush=True)
        conn = ensure_conn(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT filing_type, filing_date::text, headline, executive_summary, pricing_and_margins, capex_and_backlog
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
            try:
                biz = (yf.Ticker(ticker).info or {}).get("longBusinessSummary", "")[:3000]
                context_lines.append(f"Business Summary: {biz}")
            except Exception as e:
                print(f"  Warning fetching Yahoo summary for {ticker}: {e}", flush=True)

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

        parsed = _call_ai(prompt, CompanyProductsBatch)
        if not parsed:
            print("All AI providers exhausted — stopping cleanly.", flush=True)
            break

        conn = ensure_conn(conn)
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
                cleaned_rms = []
                for rm in prod.get("raw_materials", []):
                    cref = rm.get("commodity_ref")
                    if cref not in registry:
                        cref = None
                    cleaned_rms.append({
                        "name": rm.get("name", "Unknown material"),
                        "commodity_ref": cref,
                        "role": rm.get("role", "input"),
                        "basis": rm.get("basis") if rm.get("basis") in ("disclosed", "inferred") else "inferred",
                    })

                ds = prod.get("downstream") or {}
                dref = ds.get("demand_proxy_ref")
                if dref not in registry:
                    dref = None
                ds["demand_proxy_ref"] = dref
                ds.setdefault("process_step", "downstream processing")
                ds.setdefault("end_product", "manufactured component")
                ds.setdefault("end_demand", "industrial end-demand")
                ds.setdefault("customers", None)

                adv = prod.get("advanced") or {
                    "performance_spec": "customer-qualified specification",
                    "qualification_months": None,
                    "switching_barrier": "process requalification",
                }
                rev = prod.get("revenue") or {"basis": "business_unit", "share_pct": None}
                src = prod.get("src") or {"type": src_type, "ref": src_ref}
                if src.get("type") not in ("annual_report", "filing", "product_page", "inferred"):
                    src["type"] = src_type

                status = prod.get("status") if prod.get("status") in ("active", "divested") else "active"
                conf = prod.get("conf") if prod.get("conf") in ("high", "med", "low") else "med"
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
                        prod.get("business_unit", sub_sector or "Core Materials"),
                        status,
                        prod.get("as_of", "FY2025"),
                        json.dumps(adv),
                        json.dumps(cleaned_rms),
                        json.dumps(ds),
                        prod.get("pricing_structure"),
                        json.dumps(rev),
                        valid_peers,
                        json.dumps(src),
                        conf,
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
        print(
            f"[{ticker}] Saved {len(parsed.get('products', []))} products & {len(parsed.get('excluded', []))} excluded items.",
            flush=True,
        )

    print(
        f"Product extraction finished ({gemini_calls_used} Gemini calls, {groq_calls_used} Groq calls used).",
        flush=True,
    )
    return conn


if __name__ == "__main__":
    conn = connect_db()
    try:
        conn = run_product_extraction(conn)
    finally:
        if conn and not conn.closed:
            conn.close()
