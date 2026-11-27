import os
import re
import time
import json
from typing import List, Optional, Literal
import requests
import psycopg
import yfinance as yf
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

try:
    from ai_usage_logger import record_ai_usage
except ImportError:
    from pipeline.ai_usage_logger import record_ai_usage

DATABASE_URL = os.environ["DATABASE_URL"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
BATCH_LIMIT = int(os.environ.get("PRODUCT_BATCH_LIMIT", "15"))

GENERIC_SEGMENT_RE = re.compile(
    r"\b(business|segment|division|operations|services|activities)\b",
    re.IGNORECASE,
)

GENERIC_ADV_CATEGORY_RE = re.compile(
    r"^\s*(electronic\s+materials?|advanced\s+materials?(\s+portfolio)?|"
    r"specialty\s+materials?|performance\s+chemicals?|engineered\s+materials?|"
    r"industrial\s+materials?|semiconductor\s+materials?|battery\s+materials?)\s*$",
    re.IGNORECASE,
)

INVALID_RAW_MATERIAL_RE = re.compile(
    r"^\s*("
    r"labor|direct\s+labor|manufacturing\s+labor|"
    r"raw\s+materials?|direct\s+materials?|other\s+raw\s+materials?|"
    r"feedstocks?|industrial\s+feedstocks?|chemical\s+feedstocks?|petrochemical\s+feedstocks?|"
    r"specialty\s+chemical\s+intermediates?|chemical\s+intermediates?|intermediates?|"
    r"specialty\s+chemicals?|industrial\s+chemicals?|base\s+chemicals?|"
    r"commodities|metals?|precious\s+metals?|rare\s+earths?|minerals?|polymers?|resins?|plastic\s+resins?|"
    r"energy|electricity|power|utilities|water|freight|logistics|transportation|"
    r"packaging|consumables?|components?|parts?|equipment|tooling|scrap|general|other"
    r")\s*$"
    r"|\b(labor|raw\s+materials?|industrial\s+feedstocks?|specialty\s+chemical\s+intermediates?)\b",
    re.IGNORECASE,
)

CANONICAL_CODE_NAMES = {
    "HG=F": "Copper",
    "NG=F": "Natural Gas",
    "CL=F": "WTI Crude Oil",
    "BZ=F": "Brent Crude Oil",
    "ALI=F": "Aluminum",
    "PL=F": "Platinum",
    "SI=F": "Silver",
    "PNICKUSDM": "Nickel",
    "PCOBAUSDM": "Cobalt",
    "PNGASEUUSDM": "Natural Gas",
    "WPU101": "Iron & Steel",
    "NDPR_CN": "Neodymium-Praseodymium (NdPr) Oxide",
    "LICARB_CN": "Lithium Carbonate",
    "GALLIUM_9999": "Gallium",
    "GERMANIUM_INGOT": "Germanium",
    "POLYSILICON_N": "Polysilicon",
    "SPHERICAL_GRAPHITE": "Spherical Graphite",
    "COBALT_SULFATE": "Cobalt Sulfate",
    "CARBON_FIBER_T700": "Polyacrylonitrile (PAN) Precursor",
}

PRESERVE_ACRONYMS = {
    "wti": "WTI",
    "pan": "PAN",
    "bpa": "BPA",
    "lng": "Natural Gas",
    "ndpr": "NdPr",
    "pvc": "PVC",
    "ptfe": "PTFE",
    "pfa": "PFA",
    "peek": "PEEK",
    "pps": "PPS",
    "mdi": "MDI",
    "tdi": "TDI",
    "lipf6": "LiPF6",
    "sic": "SiC",
    "gan": "GaN",
    "gaas": "GaAs",
    "inp": "InP",
    "ccl": "CCL",
    "abf": "ABF",
    "cmp": "CMP",
    "euv": "EUV",
    "arf": "ArF",
    "oled": "OLED",
    "mlcc": "MLCC",
    "soi": "SOI",
    "hbm": "HBM",
    "ic": "IC",
    "rf": "RF",
    "ev": "EV",
    "pgm": "PGM",
    "pvoh": "PVOH",
    "ncma": "NCMA",
    "nmc": "NMC",
    "lfp": "LFP",
    "ndfeb": "NdFeB",
}

LOWER_CONNECTORS = {"and", "or", "of", "in", "for", "to", "with"}


def _to_canonical_title_case(raw: str) -> str:
    words = []
    for idx, w in enumerate(raw.split(" ")):
        low = w.lower()
        clean_low = low.strip("()")
        if clean_low in PRESERVE_ACRONYMS:
            token = PRESERVE_ACRONYMS[clean_low]
            words.append(f"({token})" if low.startswith("(") and low.endswith(")") else token)
        elif idx > 0 and low in LOWER_CONNECTORS:
            words.append(low)
        elif "-" in w:
            parts = []
            for p in w.split("-"):
                p_low = p.lower()
                if p_low in PRESERVE_ACRONYMS:
                    parts.append(PRESERVE_ACRONYMS[p_low])
                else:
                    parts.append(p.capitalize())
            words.append("-".join(parts))
        else:
            words.append(w.capitalize())
    return " ".join(words)


def normalize_raw_material_name(raw_name: str, matched_code: Optional[str] = None) -> Optional[str]:
    if matched_code and matched_code in CANONICAL_CODE_NAMES:
        return CANONICAL_CODE_NAMES[matched_code]
    cleaned = re.sub(r"\s*\([^)]*\)", " ", (raw_name or "").strip())
    cleaned = re.sub(
        r"\b(futures?|spot|benchmarks?|ind(ex|ices)|prices?|pricing|contracts?|quotes?|feedstocks?|raw\s+materials?)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^(european|global|us|u\.s\.|north\s+american|asian|chinese|domestic|imported|refined|raw|crude|primary|virgin|pure|ultra[- ]?pure|high[- ]?purity|electronic[- ]?grade|battery[- ]?grade|n[- ]?type)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—,:;")
    if not cleaned or len(cleaned) < 3 or INVALID_RAW_MATERIAL_RE.search(cleaned):
        return None
    if cleaned.lower() in ("european natural gas", "ttf natural gas", "lng", "liquefied natural gas"):
        return "Natural Gas"
    return _to_canonical_title_case(cleaned)


def normalize_adv_category(raw_cat: str, fallback_name: str = "") -> str:
    cat = re.sub(r"\s+", " ", (raw_cat or "").strip())
    if not cat or GENERIC_ADV_CATEGORY_RE.match(cat):
        alt = re.sub(r"\s+", " ", (fallback_name or "").strip())
        if alt and not GENERIC_ADV_CATEGORY_RE.match(alt):
            cat = alt
    return _to_canonical_title_case(cat) if cat else "Specialty Engineered Materials"


NON_ADV_MAT_RE = re.compile(
    r"\b("
    r"pharmaceuticals?|oncology|biologics?|vaccines?|therapeutics?|clinical\s+trials?|"
    r"prescription\s+drugs?|fertility\s+drugs?|monoclonal\s+antibod(y|ies)|cdmo|cro|"
    r"frozen\s+foods?|seasonings?|beverages?|snacks?|pet\s+food|animal\s+feed|"
    r"post-?it|scotch\s+tape|consumer\s+retail|apparel|footwear|cosmetics?|"
    r"enterprise\s+software|cloud\s+saas|avionics|building\s+automation|"
    r"insurance|banking|residential\s+housing|turnkey\s+machinery|capital\s+equipment|metrology|"
    r"recycling|"
    r"battery\s+cells?|prismatic\s+cells?|cylindrical\s+cells?|pouch\s+cells?|"
    r"battery\s+modules?|battery\s+packs?|ess\s+(racks?|systems?|batteries)|"
    r"energy\s+storage\s+systems?|all-?solid-?state\s+batter(y|ies)|"
    r"optical\s+transceivers?|wiring\s+harness(es)?|connectors?|cables?|"
    r"sensors?|actuators?|housings?|display\s+panels?|sub-?assembl(y|ies)|finished\s+components?"
    r")\b",
    re.IGNORECASE,
)

NON_MAT_CUSTOMER_RE = re.compile(
    r"\b(walmart|target|cvs|walgreens|kroger|costco|pfizer|novartis|roche|sanofi|astrazeneca|amgen|nestle|unilever)\b",
    re.IGNORECASE,
)


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

MAX_GEMINI_CALLS = 24
gemini_calls_used = 0
groq_calls_used = 0

GEMINI_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
]
exhausted_gemini_models = set()

GROQ_MODELS = [
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-20b",
    "openai/gpt-oss-safeguard-20b",
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


def fetch_live_taxonomy(conn) -> tuple[str, str]:
    """Fetches live canonical Advanced Material categories & Raw Material names from Neon."""
    conn = ensure_conn(conn)
    categories = set()
    raw_mats = set(CANONICAL_CODE_NAMES.values())

    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT category
            FROM company_products
            WHERE status = 'active' AND category IS NOT NULL;
        """)
        for (cat,) in cur.fetchall():
            clean_cat = normalize_adv_category(cat)
            if clean_cat and not GENERIC_ADV_CATEGORY_RE.match(clean_cat):
                categories.add(clean_cat)

        cur.execute("""
            SELECT DISTINCT elem->>'name'
            FROM company_products, jsonb_array_elements(raw_materials) AS elem
            WHERE status = 'active' AND elem->>'name' IS NOT NULL;
        """)
        for (rname,) in cur.fetchall():
            norm = normalize_raw_material_name(rname)
            if norm:
                raw_mats.add(norm)

        try:
            cur.execute("""
                SELECT DISTINCT commodity_name
                FROM equity_commodity_drivers
                WHERE exposure_role = 'Cost Input' AND commodity_name IS NOT NULL;
            """)
            for (cname,) in cur.fetchall():
                norm = normalize_raw_material_name(cname)
                if norm:
                    raw_mats.add(norm)
        except Exception:
            pass

    return ", ".join(sorted(categories)[:100]), ", ".join(sorted(raw_mats)[:120])


class RawMaterialSpec(BaseModel):
    name: str = Field(
        description="Specific physical chemical, metal, mineral, gas, or hydrocarbon feedstock (e.g., 'Copper', 'Natural Gas', 'Lithium Carbonate', 'Epoxy Resin'). NEVER 'Labor', 'Utilities', 'Raw Materials', 'Industrial Feedstock', 'Specialty Chemical Intermediates', or 'Spot'/'Futures'/'European' prefixes."
    )
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
        description="Explicitly disclosed customers/OEMs for this advanced material if stated in source text, else null.",
    )


class RevenueSpec(BaseModel):
    basis: str = Field(description="'product' if disclosed at product level, else 'business_unit' or 'company'.")
    share_pct: Optional[float] = Field(default=None, description="Disclosed % of company revenue if explicitly stated, else null.")


class SourceSpec(BaseModel):
    type: Literal["annual_report", "filing", "product_page", "inferred"]
    ref: Optional[str] = Field(default=None, description="Filing form/date or document reference, else null.")


class ProductItem(BaseModel):
    id: str = Field(description="Lowercase slug prefixed with ticker, e.g. 'entg-cmp-slurries'.")
    name: str = Field(description="Specific engineered material product name (never an umbrella division like 'Electronic Materials').")
    category: str = Field(description="Canonical engineered material class—reuse an existing category from the live taxonomy when applicable, or mint a specific Title Case material class if novel.")
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
    global gemini_calls_used, groq_calls_used

    if client and gemini_calls_used < MAX_GEMINI_CALLS:
        for g_model in GEMINI_MODELS:
            if g_model in exhausted_gemini_models:
                continue
            for attempt in range(2):
                try:
                    gemini_calls_used += 1
                    resp = client.models.generate_content(
                        model=g_model,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=schema,
                            temperature=0.1,
                        ),
                    )
                    tok_total = getattr(getattr(resp, "usage_metadata", None), "total_token_count", 0) or 0
                    record_ai_usage(
                        DATABASE_URL,
                        g_model,
                        "Google Gemini",
                        calls_delta=1,
                        tokens_delta=tok_total,
                    )
                    return json.loads(resp.text)
                except Exception as e:
                    err = str(e)
                    if "429" in err or "RESOURCE_EXHAUSTED" in err or "404" in err or "NOT_FOUND" in err:
                        exhausted_gemini_models.add(g_model)
                        if "429" in err or "RESOURCE_EXHAUSTED" in err:
                            record_ai_usage(
                                DATABASE_URL,
                                g_model,
                                "Google Gemini",
                                calls_delta=0,
                                quota_exhausted=True,
                            )
                        print(f"Gemini {g_model} quota/unavailable — trying next model.", flush=True)
                        break
                    if any(k in err for k in ("503", "UNAVAILABLE", "disconnected", "timed out")) and attempt == 0:
                        time.sleep(3)
                        continue
                    exhausted_gemini_models.add(g_model)
                    print(f"Gemini {g_model} error ({e}) — trying next model.", flush=True)
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

            resp_headers = dict(resp.headers)

            if resp.status_code == 429:
                err_lower = resp.text.lower()
                if "tokens per day" in err_lower or "requests per day" in err_lower:
                    print(f"Groq daily limit reached for {model_id} — switching model.", flush=True)
                    exhausted_groq_models.add(model_id)
                    record_ai_usage(
                        DATABASE_URL,
                        model_id,
                        "Groq",
                        calls_delta=0,
                        headers=resp_headers,
                        quota_exhausted=True,
                    )
                    break
                if attempt == 0:
                    print(f"Groq TPM limit on {model_id} — waiting 15s...", flush=True)
                    record_ai_usage(
                        DATABASE_URL,
                        model_id,
                        "Groq",
                        calls_delta=0,
                        headers=resp_headers,
                    )
                    time.sleep(15)
                    continue
                break

            if resp.status_code in (400, 404, 413):
                print(f"Groq {resp.status_code} on {model_id} — switching model.", flush=True)
                if resp.status_code == 404:
                    exhausted_groq_models.add(model_id)
                break

            resp.raise_for_status()
            data = resp.json()
            groq_calls_used += 1
            tok_total = data.get("usage", {}).get("total_tokens", 0) or 0
            record_ai_usage(
                DATABASE_URL,
                model_id,
                "Groq",
                calls_delta=1,
                tokens_delta=tok_total,
                headers=resp_headers,
            )
            time.sleep(2)
            return json.loads(data["choices"][0]["message"]["content"])

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


def _normalize_product_slug(raw_id: str, ticker: str, prod_name: str) -> str:
    prefix = re.sub(r"[^a-z0-9]+", "-", ticker.lower()).strip("-")
    base = re.sub(r"[^a-z0-9]+", "-", (raw_id or prod_name).lower()).strip("-")
    if not base:
        base = re.sub(r"[^a-z0-9]+", "-", prod_name.lower()).strip("-") or "product"
    if not base.startswith(f"{prefix}-"):
        base = f"{prefix}-{base}"
    return base[:80]


def run_product_extraction(conn):
    conn, registry, universe = get_registry_and_universe(conn)
    registry_guide = "\n".join([f"- {code}: {name} ({cat})" for code, (name, cat) in sorted(registry.items())])
    universe_tickers_str = ", ".join(sorted(universe.keys()))

    with conn.cursor() as cur:
        cur.execute("""
            SELECT e.ticker, e.company_name, e.sub_sector, e.adv_mat_rev_pct, e.market_cap_usd
            FROM equities_universe e
            WHERE NOT EXISTS (SELECT 1 FROM company_products p WHERE p.ticker = e.ticker)
              AND NOT EXISTS (SELECT 1 FROM company_product_profiles pr WHERE pr.ticker = e.ticker)
            ORDER BY e.market_cap_usd DESC NULLS LAST, e.ticker ASC
            LIMIT %s;
        """, (BATCH_LIMIT,))
        targets = cur.fetchall()

    print(f"Starting product extraction for {len(targets)} companies (BATCH_LIMIT={BATCH_LIMIT})...", flush=True)

    for idx, (ticker, company_name, sub_sector, adv_mat_rev_pct, _) in enumerate(targets, start=1):
        print(f"[{idx}/{len(targets)}] Extracting {ticker} ({company_name})...", flush=True)
        conn = ensure_conn(conn)
        existing_categories, existing_raw_materials = fetch_live_taxonomy(conn)

        with conn.cursor() as cur:
            cur.execute("""
                SELECT filing_type, filing_date::text, headline, executive_summary, pricing_and_margins, capex_and_backlog
                FROM company_filings_ai
                WHERE ticker = %s
                ORDER BY
                    CASE
                        WHEN filing_type IN ('10-K', '20-F', '40-F', 'Annual Report') THEN 1
                        WHEN filing_type IN ('10-Q', '6-K', 'Quarterly Report', 'Half-Year Report') THEN 2
                        ELSE 3
                    END,
                    filing_date DESC
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

        src_type = "filing" if filings and filings[0][0] in ("10-K", "10-Q", "20-F", "40-F", "6-K") else "inferred"
        src_ref = f"{filings[0][0]} ({filings[0][1]})" if filings and src_type == "filing" else None
        rev_share_note = f"{adv_mat_rev_pct}% of company revenue" if adv_mat_rev_pct is not None else "specialist segment"

        prompt = f"""
        Decompose {company_name} ({ticker}) | Assigned Advanced-Materials Sub-sector: {sub_sector} ({rev_share_note}) into specific engineered advanced-materials products.

        CONGLOMERATE & MATERIALS-VS-COMPONENTS BOUNDARY (CRITICAL):
        - Extract ONLY true engineered materials, formulated chemicals, resins, epoxy molding compounds (EMC), CMP slurries, spin-on dielectrics (SOD), OLED host/dopant materials, photoresists, specialty gases, wafers, films, fibers, powders, precursors, or specialty alloys belonging to "{sub_sector}".
        - STRICTLY EXCLUDE assembled components, discrete devices, and finished battery cells/modules/packs (e.g., prismatic/cylindrical/pouch EV battery cells, ESS racks, optical transceivers, connectors, sensors, housings, display panels, machinery/equipment) as well as pharma, food, consumer goods, and software. Put all excluded components/divisions in excluded[] instead.

        ADVANCED MATERIAL CATEGORY & NAME RESOLUTION (OPEN-ENDED CANONICAL):
        - Never use vague umbrella division names like 'Electronic Materials', 'Advanced Materials', 'Specialty Materials', or 'Performance Chemicals' as a product name or category.
        - For each product's `category`, FIRST check if it fits one of our existing canonical categories in Neon and reuse that EXACT spelling:
          [{existing_categories}]
        - ONLY if the product represents a genuinely distinct engineered material class not covered above, mint a new specific Title Case category name.

        RAW MATERIALS RESOLUTION (OPEN-ENDED CANONICAL):
        - List ONLY specific physical chemicals, metals, minerals, gases, or hydrocarbons (never 'Labor', 'Utilities', 'Raw Materials', 'Industrial Feedstock', 'Specialty Chemical Intermediates', or 'Spot'/'Futures'/'European' prefixes).
        - Reuse an EXACT existing canonical raw material name when applicable:
          [{existing_raw_materials}]
        - Otherwise mint a new specific Title Case physical feedstock name.

        STRICT INCLUSION GATE (ALL 4 MUST HOLD, OTHERWISE PUT IN excluded[]):
        1. Material-form product (chemical formulation, resin, slurry, wafer, film, fiber, powder, or alloy—NOT an assembled component, cell, module, or device).
        2. Performance-specified and qualified inside a customer's manufacturing process.
        3. Switching requires customer process requalification.
        4. Never a generic segment label (e.g., "electronic materials division", "battery business", "recycling operations").

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

        excluded_items = list(parsed.get("excluded") or [])
        saved_products = 0
        seen_product_names = set()

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
                prod_name = (prod.get("name") or "").strip()
                if not prod_name:
                    continue

                ds_raw = prod.get("downstream") or {}
                check_blob = " ".join([
                    prod_name,
                    prod.get("category") or "",
                    prod.get("business_unit") or "",
                    ds_raw.get("end_product") or "",
                    ds_raw.get("end_demand") or "",
                ])

                if (
                    GENERIC_SEGMENT_RE.search(prod_name)
                    or GENERIC_ADV_CATEGORY_RE.match(prod_name)
                    or NON_ADV_MAT_RE.search(check_blob)
                ):
                    excluded_items.append({
                        "name": prod_name,
                        "reason": "Excluded non-advanced-materials division, finished good, or generic segment.",
                    })
                    continue

                norm_prod = re.sub(r"[^a-z0-9]", "", prod_name.lower())
                if norm_prod in seen_product_names:
                    continue
                seen_product_names.add(norm_prod)

                cleaned_rms = []
                seen_rm_names = set()
                for rm in prod.get("raw_materials", []):
                    cref = rm.get("commodity_ref")
                    if cref not in registry:
                        cref = None
                    clean_rm_name = normalize_raw_material_name(rm.get("name") or "", cref)
                    if not clean_rm_name:
                        continue
                    if clean_rm_name.lower() in seen_rm_names:
                        continue
                    seen_rm_names.add(clean_rm_name.lower())
                    cleaned_rms.append({
                        "name": clean_rm_name,
                        "commodity_ref": cref,
                        "role": rm.get("role", "input"),
                        "basis": rm.get("basis") if rm.get("basis") in ("disclosed", "inferred") else "inferred",
                    })

                ds = dict(ds_raw)
                dref = ds.get("demand_proxy_ref")
                if dref not in registry:
                    dref = None
                ds["demand_proxy_ref"] = dref
                ds.setdefault("process_step", "downstream processing")
                ds.setdefault("end_product", "manufactured component")
                ds.setdefault("end_demand", "industrial end-demand")

                raw_customers = ds.get("customers")
                if isinstance(raw_customers, list):
                    filtered_custs = [
                        c.strip()
                        for c in raw_customers
                        if isinstance(c, str) and c.strip() and not NON_MAT_CUSTOMER_RE.search(c)
                    ]
                    ds["customers"] = filtered_custs or None
                else:
                    ds["customers"] = None

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
                valid_peers = list(dict.fromkeys(p for p in prod.get("peers", []) if p in universe and p != ticker))
                prod_id = _normalize_product_slug(prod.get("id", ""), ticker, prod_name)
                clean_category = normalize_adv_category(prod.get("category") or "", prod_name)

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
                        prod_id,
                        ticker,
                        prod_name,
                        clean_category,
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
                saved_products += 1

            seen_excluded = set()
            for ex in excluded_items:
                ex_name = (ex.get("name") or "").strip()
                ex_reason = (ex.get("reason") or "").strip()
                if not ex_name or not ex_reason:
                    continue
                ex_key = ex_name.lower()
                if ex_key in seen_excluded:
                    continue
                seen_excluded.add(ex_key)
                cur.execute(
                    """
                    INSERT INTO company_excluded_products (ticker, name, reason)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (ticker, name) DO UPDATE SET reason = EXCLUDED.reason, updated_at = NOW();
                    """,
                    (ticker, ex_name, ex_reason),
                )
        conn.commit()
        print(
            f"[{ticker}] Saved {saved_products} products & {len(seen_excluded)} excluded items.",
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
