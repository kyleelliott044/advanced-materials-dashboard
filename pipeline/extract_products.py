import argparse
import os
import re
import time
import json
from collections import Counter
from typing import List, Optional, Literal, Dict, Tuple
import requests
import psycopg
import yfinance as yf
from bs4 import BeautifulSoup
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

SEC_HEADERS = {
    "User-Agent": "AdvancedMaterialsResearchPlatform research@materials-macro.io",
    "Accept-Encoding": "gzip, deflate",
}

NON_US_EXCHANGE_SUFFIX_RE = re.compile(
    r"\.(DE|F|PA|AS|BR|SW|L|MI|MC|ST|HE|OL|CO|VI|WA|T|KS|KQ|TW|TWO|HK|SS|SZ|SI|AX|TO|V|SA|MX|JO|TA|SR)$",
    re.IGNORECASE,
)

# Strictly rejected non-materials across both passes
REJECTED_NON_MATERIAL_RE = re.compile(
    r"^\s*("
    r"labor|direct\s+labor|electricity|fuel|fuel\s+energy|energy|power|utilities|"
    r"freight|logistics|packaging|water|raw\s+materials?|feedstocks?|consumables?|"
    r"equipment|machinery|components?|parts?|services?|recycling|mining"
    r")\s*$",
    re.IGNORECASE,
)

# Natural gas is valid ONLY when described as chemical/process feedstock, not general fuel/utilities
NG_FEEDSTOCK_EVIDENCE_RE = re.compile(
    r"\b(feedstock|reforming|steam\s+methane|hydrogen|syngas|ammonia|cracker|ethane|carbonization|graphitization|precursor|synthesis)\b",
    re.IGNORECASE,
)

# Explicit external revenue evidence required to keep 'sells' when a material also appears in 'consumes'
EXTERNAL_SALES_EVIDENCE_RE = re.compile(
    r"\b(sell|sells|sold|third[- ]party|external\s+customers?|merchant\s+market|revenue|shipments?|commercial\s+sales|supplied\s+to\s+customers)\b",
    re.IGNORECASE,
)

# Reject generic segment umbrellas that are not specific physical materials
_UMBRELLA_ADJ = r"(?:advanced|specialty|functional|performance|electronic|engineered|industrial|chemical|semiconductor|high[- ]purity|composite|critical)"

VAGUE_UMBRELLA_PRODUCT_RE = re.compile(
    rf"^\s*(?:[a-z0-9\-&/\s]{{0,30}}\s+)?{_UMBRELLA_ADJ}(?:\s*(?:,|and|&)\s*{_UMBRELLA_ADJ}|\s+{_UMBRELLA_ADJ})*\s+materials?\s*$",
    re.IGNORECASE,
)

# Reject sentences explaining what a company does NOT make or classifies outside advanced materials
NEGATIVE_QUOTE_RE = re.compile(
    r"^\s*no\b|\b(does\s+not\s+(produce|sell|manufacture|classify)|rather\s+than\s+its\s+advanced|not\s+classified\s+under|excluded\s+from|infrastructure\s+materials)\b",
    re.IGNORECASE,
)

# Bare upstream elements/feedstocks should never be minted as T2 finished products
BARE_T0_T1_NAMES_RE = re.compile(
    r"^\s*(silicon|metallurgical\s+silicon|polysilicon|lithium|cobalt|nickel|graphite|copper|titanium|aluminum|quartz|silica|rare\s+earths?)\s*$",
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


def ensure_schema(conn):
    """Ensures `target_product` column exists on `company_material_links`."""
    conn = ensure_conn(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            ALTER TABLE company_material_links
            ADD COLUMN IF NOT EXISTS target_product TEXT;
            """
        )
    conn.commit()
    return conn


def normalize_alias_key(raw: str) -> str:
    """Canonical alias lookup key: lowercase, punctuation-stripped, '&' -> 'and'."""
    s = re.sub(r"\s*\([^)]*\)?", " ", (raw or "").strip())
    s = s.replace(".", "").replace("&", " and ").replace("/", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    return s


def load_material_registry(conn) -> Tuple[Dict[str, Tuple[int, str, str]], Dict[int, Tuple[str, str, str]]]:
    """
    Loads:
      alias_map: alias_normalized -> (material_id, canonical_name, tier)
      by_id:     material_id -> (canonical_name, tier, material_group)
    """
    conn = ensure_conn(conn)
    alias_map: Dict[str, Tuple[int, str, str]] = {}
    by_id: Dict[int, Tuple[str, str, str]] = {}

    with conn.cursor() as cur:
        cur.execute("SELECT id, canonical_name, tier, material_group FROM materials;")
        for mid, cname, tier, mgroup in cur.fetchall():
            by_id[mid] = (cname, tier, mgroup)
            alias_map[normalize_alias_key(cname)] = (mid, cname, tier)

        cur.execute("""
            SELECT a.alias_normalized, m.id, m.canonical_name, m.tier
            FROM material_aliases a
            JOIN materials m ON a.material_id = m.id;
        """)
        for alias_norm, mid, cname, tier in cur.fetchall():
            alias_map[alias_norm] = (mid, cname, tier)

    return alias_map, by_id


def resolve_material_candidate(
    raw_label: str,
    alias_map: Dict[str, Tuple[int, str, str]],
) -> Optional[Tuple[int, str, str]]:
    """Resolves a string to (material_id, canonical_name, tier) if registered."""
    k = normalize_alias_key(raw_label)
    if not k or REJECTED_NON_MATERIAL_RE.match(k):
        return None
    if k in alias_map:
        return alias_map[k]
    if k.endswith("s") and k[:-1] in alias_map:
        return alias_map[k[:-1]]
    return None


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def mint_or_get_material(
    conn,
    alias_map: Dict[str, Tuple[int, str, str]],
    by_id: Dict[int, Tuple[str, str, str]],
    canonical_name: str,
    tier: Literal["T0", "T1", "T2", "T3"],
    material_group: str,
    summary: Optional[str] = None,
) -> Optional[Tuple[int, str, str]]:
    """
    Resolves against existing materials first; if genuinely novel and tier in ('T0','T1','T2'),
    mints a new row in `materials` and `material_aliases` so future runs reuse it automatically.
    """
    existing = resolve_material_candidate(canonical_name, alias_map)
    if existing:
        return existing

    clean_name = re.sub(r"\s+", " ", (canonical_name or "").strip())
    k = normalize_alias_key(clean_name)
    if not k or len(k) < 3 or REJECTED_NON_MATERIAL_RE.match(k):
        return None

    if tier == "T3":
        return None

    slug = f"{tier.lower()}-{_slugify(clean_name)}"[:90]
    conn = ensure_conn(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO materials (slug, canonical_name, tier, material_group, summary)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (canonical_name) DO UPDATE SET
                tier = EXCLUDED.tier
            RETURNING id, canonical_name, tier;
            """,
            (slug, clean_name, tier, material_group or "General", summary),
        )
        row = cur.fetchone()
        if not row:
            return None
        mid, cname, saved_tier = row
        cur.execute(
            """
            INSERT INTO material_aliases (alias_normalized, material_id)
            VALUES (%s, %s)
            ON CONFLICT (alias_normalized) DO NOTHING;
            """,
            (k, mid),
        )
    conn.commit()
    alias_map[k] = (mid, cname, saved_tier)
    by_id[mid] = (cname, saved_tier, material_group or "General")
    return (mid, cname, saved_tier)


# ---------------------------------------------------------------------------
# SEC Section Extraction (Pass A: Item 1 + Segment Notes | Pass B: Inputs/Risk/COGS)
# ---------------------------------------------------------------------------

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


def _extract_keyword_windows(text: str, patterns: list[str], window: int = 1800, max_total: int = 11000) -> str:
    chunks = []
    used_ranges = []
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            start = max(0, m.start() - 300)
            end = min(len(text), m.end() + window)
            if any(not (end < s or start > e) for s, e in used_ranges):
                continue
            used_ranges.append((start, end))
            chunks.append(text[start:end])
            if sum(len(c) for c in chunks) >= max_total:
                return " ... ".join(chunks)
    return " ... ".join(chunks)


def fetch_split_sec_sections(
    ticker: str,
    company_name: str,
    sec_by_ticker: dict,
    sec_by_name: dict,
) -> Optional[Tuple[str, str, str]]:
    """
    Fetches the latest 10-K / 20-F / 40-F raw filing and splits it into:
      (pass_a_products_text, pass_b_inputs_text, source_ref)
    - Pass A sees ONLY Item 1 (Business) and Segment Footnotes.
    - Pass B sees ONLY Raw Materials / Sources of Supply, Cost of Sales, and Commodity Risk.
    """
    cik_pair = _lookup_sec_cik(ticker, company_name, sec_by_ticker, sec_by_name)
    if not cik_pair:
        return None

    cik_padded, cik_unpadded = cik_pair
    sub_url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    sub_resp = requests.get(sub_url, headers=SEC_HEADERS, timeout=15)
    sub_resp.raise_for_status()
    recent = sub_resp.json()["filings"]["recent"]

    target_idx = next((i for i, f in enumerate(recent["form"]) if f in ("10-K", "20-F", "40-F")), None)
    if target_idx is None:
        return None

    form = recent["form"][target_idx]
    f_date = recent["filingDate"][target_idx]
    acc_num = recent["accessionNumber"][target_idx]
    doc_name = recent["primaryDocument"][target_idx]

    url = f"https://www.sec.gov/Archives/edgar/data/{cik_unpadded}/{acc_num.replace('-', '')}/{doc_name}"
    resp = requests.get(url, headers=SEC_HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    text = re.sub(r"\s+", " ", soup.get_text(separator=" ", strip=True))

    if len(text) < 2000:
        return None

    # Pass A: Item 1 Business + Segment Footnotes ONLY
    item1_matches = list(re.finditer(r"Item\s+1\.\s+Business", text, re.IGNORECASE))
    item1_slice = ""
    if item1_matches:
        start1 = item1_matches[-1].start()
        item1_slice = text[start1 : start1 + 11000]
    else:
        item1_slice = text[2000:13000]

    segment_windows = _extract_keyword_windows(
        text,
        [
            r"(reportable\s+segments?|segment\s+information|note\s+\d+[\s\.\-–]+segments?)",
            r"(principal\s+products|products\s+and\s+services|product\s+lines|net\s+sales\s+by\s+product)",
        ],
        window=2000,
        max_total=7000,
    )
    pass_a_text = f"{item1_slice}\n\n--- SEGMENT & PRODUCT NOTES ---\n{segment_windows}"[:17000]

    # Pass B: Raw Materials, Sources of Supply, Cost of Sales, and Commodity Market Risk ONLY
    pass_b_text = _extract_keyword_windows(
        text,
        [
            r"(raw\s+materials|sources\s+and\s+availability\s+of\s+raw\s+materials|principal\s+suppliers|feedstocks?)",
            r"(cost\s+of\s+(sales|goods\s+sold|products\s+sold)|gross\s+margin|input\s+costs)",
            r"(commodity\s+(price\s+)?risk|quantitative\s+and\s+qualitative\s+disclosures\s+about\s+market\s+risk|hedging)",
        ],
        window=2200,
        max_total=15000,
    )
    if len(pass_b_text) < 600:
        pass_b_text = item1_slice[:10000]

    return pass_a_text, pass_b_text[:16000], f"{form} ({f_date})"


# ---------------------------------------------------------------------------
# Pydantic Schemas for Split-Pass Extraction
# ---------------------------------------------------------------------------

class SoldMaterialCandidate(BaseModel):
    canonical_material: str = Field(
        description="Canonical material name from the provided T0/T1/T2 list if matched, or a specific Title Case material name if novel. Never an end-market ('Aerospace'), process ('Mining', 'Refining'), or vague umbrella ('Electronic Materials', 'Advanced Materials')."
    )
    product_line_name: str = Field(
        description="Company's specific commercial product line or material grade name in clean Title Case (e.g., '300mm Epitaxial Silicon Wafers', 'Battery-Grade Lithium Hydroxide'). Never a segment umbrella like 'Advanced Materials'."
    )
    classification: Literal["sold_T2_material", "sold_T0_T1_material", "sold_T3_non_material"] = Field(
        description="Classify strictly: 'sold_T2_material' for customer-qualified engineered materials; 'sold_T0_T1_material' for traded ores, metals, or refined intermediates (polysilicon, lithium carbonate, NdPr oxide); 'sold_T3_non_material' for assembled cells, connectors, sensors, equipment, or non-materials."
    )
    material_group: str = Field(
        description="Group name from the provided material list (e.g. 'Wafers and substrates', 'Battery', 'Aerospace and industrial', 'Refined intermediates')."
    )
    business_unit: str = Field(description="Disclosed reporting segment or business unit.")
    performance_spec: Optional[str] = Field(
        default=None,
        description="Measurable performance parameter if stated in text, else null.",
    )
    end_demand: Optional[str] = Field(
        default=None,
        description="End-market application if stated in text, else null.",
    )
    source_quote: str = Field(
        description="Verbatim 10-30 word quote from the Item 1 / Segment text proving the company manufactures and sells this material."
    )
    confidence: Literal["high", "med", "low"]


class PassASoldBatch(BaseModel):
    sold_candidates: List[SoldMaterialCandidate] = Field(default_factory=list)


class ConsumedInputCandidate(BaseModel):
    canonical_material: str = Field(
        description="Canonical T0/T1 raw input name from the provided list (e.g., 'Copper', 'Natural Gas', 'Silica Sand', 'Lithium Carbonate', 'Polysilicon'), or specific physical feedstock if novel."
    )
    tier_guess: Literal["T0", "T1"] = Field(
        description="'T0' for primary ore/metal/mineral/hydrocarbon/basic chemical; 'T1' for refined intermediate (polysilicon, lithium carbonate, nickel sulfate, PAN precursor)."
    )
    material_group: str = Field(
        description="Group from the raw input list (e.g. 'Base and battery metals', 'Minor metals', 'Industrial minerals', 'Energy and hydrocarbons', 'Basic petrochemicals', 'Inorganic chemicals', 'Refined intermediates')."
    )
    target_products: List[str] = Field(
        default_factory=list,
        description="Exact product_line_name(s) from the company's SOLD PRODUCTS list that chemically/physically consume this input. Only include products that actually require this feedstock; return [] if unclear.",
    )
    is_feedstock_not_fuel: bool = Field(
        default=True,
        description="For Natural Gas or hydrocarbons, True ONLY if used as a chemical/reforming feedstock or direct thermal conversion input—False if merely general facility heating/electricity.",
    )
    source_quote: str = Field(
        description="Verbatim 10-30 word quote from the Raw Materials / COGS / Risk section proving the company purchases or consumes this input."
    )
    confidence: Literal["high", "med", "low"]


class PassBConsumedBatch(BaseModel):
    consumed_inputs: List[ConsumedInputCandidate] = Field(default_factory=list)


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


def _format_taxonomy_prompt_lists(by_id: Dict[int, Tuple[str, str, str]]) -> Tuple[str, str]:
    t2_list = sorted([cname for cname, tier, _ in by_id.values() if tier == "T2"])
    t0_t1_list = sorted([f"{cname} ({tier})" for cname, tier, _ in by_id.values() if tier in ("T0", "T1")])
    return ", ".join(t2_list), ", ".join(t0_t1_list)


def run_split_extraction(conn, force_tickers: Optional[List[str]] = None):
    conn = ensure_schema(conn)
    alias_map, by_id = load_material_registry(conn)
    t2_vocab, t0_t1_vocab = _format_taxonomy_prompt_lists(by_id)
    sec_by_ticker, sec_by_name = fetch_sec_ticker_map()

    with conn.cursor() as cur:
        if force_tickers:
            cur.execute(
                """
                SELECT ticker, company_name, sub_sector, adv_mat_rev_pct
                FROM equities_universe
                WHERE ticker = ANY(%s)
                ORDER BY ticker ASC;
                """,
                (force_tickers,),
            )
        else:
            cur.execute(
                """
                SELECT e.ticker, e.company_name, e.sub_sector, e.adv_mat_rev_pct
                FROM equities_universe e
                WHERE NOT EXISTS (SELECT 1 FROM company_material_links l WHERE l.ticker = e.ticker)
                ORDER BY e.market_cap_usd DESC NULLS LAST, e.ticker ASC
                LIMIT %s;
                """,
                (BATCH_LIMIT,),
            )
        targets = cur.fetchall()

    print(f"Starting split-pass material link extraction for {len(targets)} companies...", flush=True)
    stats = Counter()

    for idx, (ticker, company_name, sub_sector, adv_mat_rev_pct) in enumerate(targets, start=1):
        print(f"[{idx}/{len(targets)}] Extracting {ticker} ({company_name})...", flush=True)
        conn = ensure_conn(conn)

        split_sec = None
        try:
            split_sec = fetch_split_sec_sections(ticker, company_name, sec_by_ticker, sec_by_name)
        except Exception as e:
            print(f"  SEC fetch warning for {ticker}: {e}", flush=True)

        if split_sec:
            pass_a_text, pass_b_text, src_ref = split_sec
        else:
            # Fallback for non-SEC international equities: use filing summaries + Yahoo profile
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT filing_type, filing_date::text, headline, executive_summary, pricing_and_margins
                    FROM company_filings_ai
                    WHERE ticker = %s
                    ORDER BY filing_date DESC
                    LIMIT 2;
                    """,
                    (ticker,),
                )
                f_rows = cur.fetchall()
            biz_summary = ""
            try:
                biz_summary = (yf.Ticker(ticker).info or {}).get("longBusinessSummary", "")[:3500]
            except Exception:
                pass
            pass_a_text = (
                f"Business Profile: {biz_summary}\n"
                + "\n".join([f"[{r[0]} {r[1]}] {r[2]}: {r[3]}" for r in f_rows])
            )[:14000]
            pass_b_text = (
                "\n".join([f"[{r[0]} {r[1]}] Margins/Inputs: {r[4] or ''} | {r[3]}" for r in f_rows])
                + f"\nProfile: {biz_summary}"
            )[:14000]
            src_ref = f"{f_rows[0][0]} ({f_rows[0][1]})" if f_rows else "Profile"

        if len(pass_a_text.strip()) < 100:
            print(f"  Skipping {ticker}: insufficient source text.", flush=True)
            continue

        rev_note = f"{adv_mat_rev_pct}% of revenue" if adv_mat_rev_pct is not None else "specialist segment"

        # -------------------------------------------------------------------
        # PASS A: SOLD MATERIALS ONLY (Item 1 + Segment Notes)
        # -------------------------------------------------------------------
        prompt_a = f"""
        You are an institutional advanced materials analyst.
        Analyze ONLY the Item 1 Business & Segment text below for {company_name} ({ticker}) | Sub-sector: {sub_sector} ({rev_note}).
        Extract every physical material that {company_name} MANUFACTURES AND SELLS to customers.

        TIER & CLASSIFICATION RULES:
        - Classify each candidate into one of three buckets:
          1. 'sold_T2_material': Engineered material specified and qualified in the customer's manufacturing process. Canonical T2 list:
             [{t2_vocab}]
          2. 'sold_T0_T1_material': Primary ore/metal/mineral (T0) or refined/traded intermediate not customer-process-qualified (T1, e.g., Polysilicon, Lithium Carbonate and Hydroxide, NdPr Oxide). Canonical T0/T1 list:
             [{t0_t1_vocab}]
          3. 'sold_T3_non_material': Assembled battery cells/modules/packs, connectors, sensors, optical transceivers, displays, machinery, pharma, food, or software (EXCLUDE from products).
        - Match to an EXACT canonical name from the lists above whenever applicable; only mint a new specific material name if genuinely distinct.
        - ADVANCED MATERIALS ONLY: Extract ONLY high-value, customer-qualified advanced materials (semiconductor, battery, aerospace/defense, optical, magnetic, high-performance fluoropolymers/composites, specialty electronic chemicals). EXCLUDE commodity construction/infrastructure materials (e.g., commodity PVC, POVAL/polyvinyl alcohol, cement, basic packaging resins) unless explicitly described as high-purity/semiconductor/battery grade.
        - SPECIFIC MATERIAL NAMES ONLY: NEVER emit generic segment names or umbrellas like 'Advanced Materials', 'Performance Materials', 'Electronic Materials', 'Specialty Materials', or 'Electronic Specialty and Advanced Materials'. Every `canonical_material` and `product_line_name` must name an actual physical chemistry, alloy, ceramic, crystal, or polymer class.
        - ONE ENTRY PER MATERIAL CLASS: Do NOT split a single material class into multiple rows just because it serves different end-markets (e.g., emit ONE entry for 'Silicones', NOT both 'Silicones' and 'Semiconductor Silicones'). Combine all end-markets into `end_demand`.
        - NEVER emit bare upstream elements or feedstocks ('Silicon', 'Lithium', 'Cobalt', 'Nickel', 'Copper', 'Quartz') as 'sold_T2_material'.
        - POSITIVE EVIDENCE ONLY: Ignore sentences stating what a company does NOT make or classifies outside its advanced materials segments (never use a sentence starting with "No," as a `source_quote`).
        - Format `product_line_name` in consistent Title Case (preserving chemical acronyms like NdFeB, SiC, PVC, PTFE, SOI, CMP, OLED).
        - NEVER list end-markets ('Aerospace', 'Mobility', 'Life Science', 'Optics') or processes ('Mining', 'Refining') as materials.
        - Every item MUST include a verbatim `source_quote` from the text below proving the company produces/sells it.

        ITEM 1 & SEGMENT TEXT ({src_ref}):
        {pass_a_text}
        """

        parsed_a = _call_ai(prompt_a, PassASoldBatch)
        if not parsed_a:
            print("All AI providers exhausted — stopping cleanly.", flush=True)
            break

        sold_product_names = sorted(
            {
                (cand.get("product_line_name") or cand.get("canonical_material") or "").strip()
                for cand in parsed_a.get("sold_candidates", [])
                if cand.get("classification") != "sold_T3_non_material"
                and (cand.get("product_line_name") or cand.get("canonical_material"))
                and not VAGUE_UMBRELLA_PRODUCT_RE.match(
                    (cand.get("product_line_name") or cand.get("canonical_material") or "").strip()
                )
            }
        )
        sold_products_str = ", ".join(sold_product_names) if sold_product_names else "None disclosed"

        # -------------------------------------------------------------------
        # PASS B: CONSUMED RAW INPUTS ONLY (Raw Materials / COGS / Risk)
        # -------------------------------------------------------------------
        prompt_b = f"""
        You are an institutional supply-chain & commodity risk analyst.
        Analyze ONLY the Raw Materials, Cost of Sales, and Commodity Risk text below for {company_name} ({ticker}).
        Extract every physical T0 or T1 raw material/feedstock that {company_name} PURCHASES OR CONSUMES as a manufacturing input.

        SOLD PRODUCTS CONTEXT (from Item 1):
        [{sold_products_str}]

        STRICT RULES:
        - Match each consumed input to an EXACT canonical name from our T0/T1 registry list when applicable:
          [{t0_t1_vocab}]
        - PRODUCT ATTRIBUTION (`target_products`): Map each consumed input ONLY to the exact product name(s) in SOLD PRODUCTS CONTEXT that physically/chemically require that input (e.g., Methanol & Caustic Soda -> Silicones, NOT Rare Earth Magnets; Quartz -> Semiconductor Silicon & Silicones, NOT Rare Earth Magnets). If an input cannot be reliably linked to a specific product, return `[]`.
        - STRICTLY FORBIDDEN: 'Labor', 'Electricity', 'Fuel', 'Energy', 'Freight', 'Packaging', 'Utilities', 'Raw Materials', 'Feedstock'.
        - NATURAL GAS RULE: Include 'Natural Gas' ONLY if the text describes it as a chemical/reforming feedstock or direct thermal conversion input (set `is_feedstock_not_fuel=true`). If it is merely facility heating/fuel, set `is_feedstock_not_fuel=false` or omit it.
        - Every item MUST include a verbatim `source_quote` proving purchase, supply, or input cost exposure.

        RAW MATERIALS, COGS & RISK TEXT ({src_ref}):
        {pass_b_text}
        """

        parsed_b = _call_ai(prompt_b, PassBConsumedBatch)
        if not parsed_b:
            print("All AI providers exhausted — stopping cleanly.", flush=True)
            break

        # -------------------------------------------------------------------
        # RESOLVE TIERS, DETECT CAPTIVE OVERLAP & SAVE LINKS
        # -------------------------------------------------------------------
        sold_items: Dict[int, List[dict]] = {}
        for cand in parsed_a.get("sold_candidates", []):
            if cand.get("classification") == "sold_T3_non_material":
                stats["excluded_T3"] += 1
                continue

            raw_mat_label = (cand.get("canonical_material") or "").strip()
            quote = (cand.get("source_quote") or "").strip()
            if (
                not raw_mat_label
                or len(quote) < 8
                or VAGUE_UMBRELLA_PRODUCT_RE.match(raw_mat_label)
                or NEGATIVE_QUOTE_RE.search(quote)
            ):
                stats["excluded_vague_or_negative"] += 1
                continue

            resolved = resolve_material_candidate(raw_mat_label, alias_map)
            if not resolved:
                if BARE_T0_T1_NAMES_RE.match(raw_mat_label):
                    inferred_tier = "T0"
                else:
                    inferred_tier = "T2" if cand.get("classification") == "sold_T2_material" else "T1"
                resolved = mint_or_get_material(
                    conn,
                    alias_map,
                    by_id,
                    raw_mat_label,
                    inferred_tier,
                    cand.get("material_group") or "Engineered Materials",
                )
            if not resolved:
                continue

            mid, cname, db_tier = resolved
            if db_tier == "T3" or VAGUE_UMBRELLA_PRODUCT_RE.match(cname):
                stats["excluded_T3"] += 1
                continue

            raw_prod_line = (cand.get("product_line_name") or "").strip()
            if not raw_prod_line or VAGUE_UMBRELLA_PRODUCT_RE.match(raw_prod_line):
                raw_prod_line = cname

            sold_items.setdefault(mid, []).append({
                "canonical_name": cname,
                "tier": db_tier,
                "product_name": raw_prod_line[:160],
                "business_unit": (cand.get("business_unit") or sub_sector or "Core Materials")[:120],
                "performance_spec": cand.get("performance_spec"),
                "end_demand": cand.get("end_demand"),
                "source_quote": quote[:400],
                "confidence": cand.get("confidence") if cand.get("confidence") in ("high", "med", "low") else "med",
            })

        # Collapse redundant same-material_id entries where one product_name is a substring of another
        for mid, entries in list(sold_items.items()):
            if len(entries) > 1:
                entries.sort(key=lambda x: len(x["product_name"]))
                merged_demands = []
                for e in entries:
                    ed = (e.get("end_demand") or "").strip()
                    if ed and ed not in merged_demands:
                        merged_demands.append(ed)
                primary = entries[0]
                if merged_demands:
                    primary["end_demand"] = ", ".join(merged_demands)[:240]
                sold_items[mid] = [primary]

        # consumed_items: material_id -> dict metadata
        consumed_items: Dict[int, dict] = {}
        for inp in parsed_b.get("consumed_inputs", []):
            raw_inp_label = (inp.get("canonical_material") or "").strip()
            quote = (inp.get("source_quote") or "").strip()
            if not raw_inp_label or len(quote) < 8:
                continue

            resolved = resolve_material_candidate(raw_inp_label, alias_map)
            if not resolved:
                tier_guess = inp.get("tier_guess") if inp.get("tier_guess") in ("T0", "T1") else "T0"
                resolved = mint_or_get_material(
                    conn,
                    alias_map,
                    by_id,
                    raw_inp_label,
                    tier_guess,
                    inp.get("material_group") or "Raw Inputs",
                )
            if not resolved:
                continue

            mid, cname, db_tier = resolved
            if db_tier not in ("T0", "T1"):
                continue

            if cname.lower() == "natural gas":
                if not inp.get("is_feedstock_not_fuel", False) and not NG_FEEDSTOCK_EVIDENCE_RE.search(quote):
                    stats["rejected_ng_fuel"] += 1
                    continue

            target_prods = [
                p.strip()[:160]
                for p in (inp.get("target_products") or [])
                if isinstance(p, str) and p.strip()
            ]
            consumed_items[mid] = {
                "canonical_name": cname,
                "tier": db_tier,
                "target_products": target_prods,
                "source_quote": quote[:400],
                "confidence": inp.get("confidence") if inp.get("confidence") in ("high", "med", "low") else "med",
            }

        # Rule 4: Flag captive overlap (`makes_captive`) when same material is both sold and consumed
        link_rows = []
        all_mids = set(sold_items.keys()) | set(consumed_items.keys())
        for mid in all_mids:
            in_sold = mid in sold_items
            in_cons = mid in consumed_items

            if in_sold and in_cons:
                s_entries = sold_items[mid]
                has_external_sales = any(
                    EXTERNAL_SALES_EVIDENCE_RE.search(e["source_quote"]) for e in s_entries
                )
                if has_external_sales:
                    for e in s_entries:
                        link_rows.append((
                            ticker, mid, "sells", e["product_name"], e["business_unit"],
                            e["performance_spec"], e["end_demand"], None,
                            f"Item 1 / Segment ({src_ref})", e["source_quote"], e["confidence"]
                        ))
                    c_entry = consumed_items[mid]
                    target_str = " | ".join(c_entry["target_products"]) or None
                    link_rows.append((
                        ticker, mid, "consumes", None, None, None, None, target_str,
                        f"Raw Materials / Risk ({src_ref})", c_entry["source_quote"], c_entry["confidence"]
                    ))
                else:
                    e = s_entries[0]
                    stats["captive_flagged"] += 1
                    link_rows.append((
                        ticker, mid, "makes_captive", e["product_name"], e["business_unit"],
                        e["performance_spec"], e["end_demand"], None,
                        f"Captive Overlap ({src_ref})", e["source_quote"], e["confidence"]
                    ))
            elif in_sold:
                for e in sold_items[mid]:
                    link_rows.append((
                        ticker, mid, "sells", e["product_name"], e["business_unit"],
                        e["performance_spec"], e["end_demand"], None,
                        f"Item 1 / Segment ({src_ref})", e["source_quote"], e["confidence"]
                    ))
            elif in_cons:
                c_entry = consumed_items[mid]
                target_str = " | ".join(c_entry["target_products"]) or None
                link_rows.append((
                    ticker, mid, "consumes", None, None, None, None, target_str,
                    f"Raw Materials / Risk ({src_ref})", c_entry["source_quote"], c_entry["confidence"]
                ))

        conn = ensure_conn(conn)
        with conn.cursor() as cur:
            if force_tickers:
                cur.execute("DELETE FROM company_material_links WHERE ticker = %s;", (ticker,))

            cur.executemany(
                """
                INSERT INTO company_material_links (
                    ticker, material_id, role, product_name, business_unit,
                    performance_spec, end_demand, target_product, source_section, source_quote, confidence
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker, material_id, role, COALESCE(product_name, '')) DO UPDATE SET
                    business_unit = COALESCE(EXCLUDED.business_unit, company_material_links.business_unit),
                    performance_spec = COALESCE(EXCLUDED.performance_spec, company_material_links.performance_spec),
                    end_demand = COALESCE(EXCLUDED.end_demand, company_material_links.end_demand),
                    target_product = COALESCE(EXCLUDED.target_product, company_material_links.target_product),
                    source_section = EXCLUDED.source_section,
                    source_quote = EXCLUDED.source_quote,
                    confidence = EXCLUDED.confidence,
                    updated_at = NOW();
                """,
                link_rows,
            )
        conn.commit()
        stats["links_saved"] += len(link_rows)
        print(
            f"[{ticker}] Saved {len(link_rows)} links (sells={sum(1 for r in link_rows if r[2]=='sells')}, "
            f"consumes={sum(1 for r in link_rows if r[2]=='consumes')}, "
            f"captive={sum(1 for r in link_rows if r[2]=='makes_captive')}).",
            flush=True,
        )

    print(
        f"Split-pass extraction complete ({gemini_calls_used} Gemini calls, {groq_calls_used} Groq calls). "
        f"Stats: {dict(stats)}",
        flush=True,
    )
    return conn


def run_eval_check(conn):
    """
    Rule 6: Evaluates role & tier assignments across known benchmark companies
    (e.g., ENTG, ALB, WCH.DE, MP, LYC.AX, SUMCO, HXL, CRS, LIN) to verify precision.
    """
    conn = ensure_conn(conn)
    eval_tickers = ["ENTG", "ALB", "SQM", "MP", "HXL", "CRS", "LIN", "DD", "APD", "ECL"]
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT l.ticker, m.tier, m.canonical_name, l.role, l.product_name, l.target_product, l.source_quote
            FROM company_material_links l
            JOIN materials m ON l.material_id = m.id
            WHERE l.ticker = ANY(%s)
            ORDER BY l.ticker, l.role, m.tier, m.canonical_name;
            """,
            (eval_tickers,),
        )
        rows = cur.fetchall()

    print(f"=== EVAL CHECK ({len(rows)} links across benchmark tickers) ===", flush=True)
    for tk, tier, cname, role, pname, tprod, quote in rows:
        print(
            f"  {tk:<8} | {tier} | {role:<13} | {cname:<34} | prod={pname or '-':<24} | target={tprod or '-':<24} | {quote[:50]}...",
            flush=True,
        )
    return conn


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Split-pass T0-T3 material link extraction.")
    parser.add_argument("--tickers", type=str, default="", help="Comma-separated tickers to force re-extract (e.g. ENTG,ALB,MP).")
    parser.add_argument("--eval", action="store_true", help="Print role/tier assignments for benchmark eval companies.")
    args = parser.parse_args()

    conn = connect_db()
    try:
        if args.eval:
            conn = run_eval_check(conn)
        else:
            forced = [t.strip().upper() for t in args.tickers.split(",") if t.strip()] or None
            conn = run_split_extraction(conn, force_tickers=forced)
    finally:
        if conn and not conn.closed:
            conn.close()
