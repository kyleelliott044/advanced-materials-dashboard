import argparse
import json
import os
import re
import time
from collections import Counter
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

import psycopg
import requests
import yfinance as yf
from bs4 import BeautifulSoup
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

try:
    from ai_usage_logger import record_ai_usage
except ImportError:
    from pipeline.ai_usage_logger import record_ai_usage

DATABASE_URL = os.environ["DATABASE_URL"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "")
BATCH_LIMIT = int(os.environ.get("CATALOG_BATCH_LIMIT", "10"))
MAX_PAGES_PER_COMPANY = int(os.environ.get("MAX_PAGES_PER_COMPANY", "14"))

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# URL path patterns likely to contain product catalogs
PRODUCT_URL_INCLUDE_RE = re.compile(
    r"/(products?|materials?|solutions?|chemicals?|polymers?|semiconductor|electronics?|metals?|alloys?|ceramics?|coatings?|specialty|advanced|portfolio|catalog|offerings?|business)(/|$|-|_)",
    re.IGNORECASE,
)

# URL path patterns to strictly ignore
PRODUCT_URL_EXCLUDE_RE = re.compile(
    r"/(investor|ir|news|press|media|career|jobs?|about|sustainability|esg|contact|legal|privacy|cookie|login|cart|support|faq|events?|blog)(/|$|-|_)",
    re.IGNORECASE,
)

# Reject generic segment umbrellas that are not specific physical materials
_UMBRELLA_ADJ = (
    r"(?:advanced|specialty|functional|performance|electronic|engineered|"
    r"industrial|chemical|semiconductor|high[- ]purity|composite|critical)"
)
VAGUE_UMBRELLA_PRODUCT_RE = re.compile(
    rf"^\s*(?:[a-z0-9\-&/\s]{{0,30}}\s+)?{_UMBRELLA_ADJ}"
    rf"(?:\s*(?:,|and|&)\s*{_UMBRELLA_ADJ}|\s+{_UMBRELLA_ADJ})*\s+materials?\s*$",
    re.IGNORECASE,
)

BARE_T0_T1_NAMES_RE = re.compile(
    r"^\s*(silicon|metallurgical\s+silicon|polysilicon|lithium|cobalt|nickel|"
    r"graphite|copper|titanium|aluminum|quartz|silica|rare\s+earths?)\s*$",
    re.IGNORECASE,
)

GEMINI_MODELS = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]
GROQ_MODELS = ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"]
exhausted_gemini_models: Set[str] = set()
exhausted_groq_models: Set[str] = set()
gemini_calls_used = 0
groq_calls_used = 0

gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


# ---------------------------------------------------------------------------
# Database Helpers
# ---------------------------------------------------------------------------

def connect_db():
    return psycopg.connect(
        DATABASE_URL,
        autocommit=True,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5,
    )


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS company_product_sources (
              ticker TEXT PRIMARY KEY,
              domain TEXT NOT NULL,
              catalog_urls JSONB NOT NULL DEFAULT '[]'::jsonb,
              last_crawled_at TIMESTAMPTZ
            );
            """
        )
        cur.execute(
            """
            ALTER TABLE company_material_links
            ADD COLUMN IF NOT EXISTS target_product TEXT;
            """
        )
    conn.commit()
    return conn


def normalize_alias_key(raw: str) -> str:
    s = re.sub(r"\s*\([^)]*\)?", " ", (raw or "").strip())
    s = s.replace(".", "").replace("&", " and ").replace("/", " and ")
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def load_material_registry(conn) -> Tuple[Dict[str, Tuple[int, str, str]], Dict[int, Tuple[str, str, str]]]:
    alias_map: Dict[str, Tuple[int, str, str]] = {}
    by_id: Dict[int, Tuple[str, str, str]] = {}
    with conn.cursor() as cur:
        cur.execute("SELECT id, canonical_name, tier, material_group FROM materials;")
        for mid, cname, tier, mgroup in cur.fetchall():
            by_id[mid] = (cname, tier, mgroup)
            alias_map[normalize_alias_key(cname)] = (mid, cname, tier)
        cur.execute(
            """
            SELECT a.alias_normalized, m.id, m.canonical_name, m.tier
            FROM material_aliases a
            JOIN materials m ON a.material_id = m.id;
            """
        )
        for alias_norm, mid, cname, tier in cur.fetchall():
            alias_map[alias_norm] = (mid, cname, tier)
    return alias_map, by_id


def mint_or_get_t2_material(
    conn,
    alias_map: Dict[str, Tuple[int, str, str]],
    by_id: Dict[int, Tuple[str, str, str]],
    canonical_name: str,
    material_group: str,
) -> Optional[Tuple[int, str, str]]:
    k = normalize_alias_key(canonical_name)
    if not k or len(k) < 3:
        return None
    if k in alias_map:
        return alias_map[k]
    if k.endswith("s") and k[:-1] in alias_map:
        return alias_map[k[:-1]]

    clean_name = re.sub(r"\s+", " ", canonical_name.strip())
    slug = f"t2-{re.sub(r'[^a-z0-9]+', '-', clean_name.lower()).strip('-')}"[:90]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO materials (slug, canonical_name, tier, material_group)
            VALUES (%s, %s, 'T2', %s)
            ON CONFLICT (canonical_name) DO UPDATE SET
                canonical_name = EXCLUDED.canonical_name
            RETURNING id, canonical_name, tier;
            """,
            (slug, clean_name, material_group or "Engineered Materials"),
        )
        row = cur.fetchone()
        if not row:
            return None
        mid, cname, tier = row
        cur.execute(
            """
            INSERT INTO material_aliases (alias_normalized, material_id)
            VALUES (%s, %s)
            ON CONFLICT (alias_normalized) DO NOTHING;
            """,
            (k, mid),
        )
    conn.commit()
    alias_map[k] = (mid, cname, tier)
    by_id[mid] = (cname, tier, material_group or "Engineered Materials")
    return (mid, cname, tier)


# ---------------------------------------------------------------------------
# Stage 1 & 2: Official Product Page Discovery + Depth-2 HTML Scraper
# ---------------------------------------------------------------------------

def _get_company_domain(ticker: str) -> Optional[str]:
    try:
        website = (yf.Ticker(ticker).info or {}).get("website", "")
        if website:
            parsed = urlparse(website if "://" in website else f"https://{website}")
            host = parsed.netloc.lower()
            return host[4:] if host.startswith("www.") else host
    except Exception:
        pass
    return None


def _is_same_domain(url: str, domain: str) -> bool:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host == domain or host.endswith(f".{domain}")


def _discover_from_sitemap(domain: str) -> List[str]:
    found: List[str] = []
    for proto in ("https", "http"):
        sm_url = f"{proto}://{domain}/sitemap.xml"
        try:
            resp = requests.get(sm_url, headers=HTTP_HEADERS, timeout=12)
            if resp.status_code != 200 or "<loc>" not in resp.text:
                continue
            locs = re.findall(r"<loc>\s*(https?://[^<\s]+)\s*</loc>", resp.text, re.IGNORECASE)
            # Handle sitemap index files
            sub_sitemaps = [u for u in locs if "sitemap" in u.lower() and u.endswith(".xml")][:4]
            for sub in sub_sitemaps:
                try:
                    s_resp = requests.get(sub, headers=HTTP_HEADERS, timeout=10)
                    if s_resp.status_code == 200:
                        locs.extend(re.findall(r"<loc>\s*(https?://[^<\s]+)\s*</loc>", s_resp.text, re.IGNORECASE))
                except Exception:
                    continue

            for u in locs:
                path = urlparse(u).path
                if (
                    _is_same_domain(u, domain)
                    and PRODUCT_URL_INCLUDE_RE.search(path)
                    and not PRODUCT_URL_EXCLUDE_RE.search(path)
                    and not u.lower().endswith((".pdf", ".jpg", ".png", ".xml"))
                ):
                    found.append(u.split("#")[0].rstrip("/"))
            if found:
                break
        except Exception:
            continue
    return list(dict.fromkeys(found))


def _discover_from_homepage(domain: str) -> List[str]:
    found: List[str] = []
    for base in (f"https://www.{domain}", f"https://{domain}"):
        try:
            resp = requests.get(base, headers=HTTP_HEADERS, timeout=15)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = urljoin(base, a["href"]).split("#")[0].rstrip("/")
                path = urlparse(href).path
                label = a.get_text(" ", strip=True)
                if (
                    _is_same_domain(href, domain)
                    and (PRODUCT_URL_INCLUDE_RE.search(path) or PRODUCT_URL_INCLUDE_RE.search(f"/{label}/"))
                    and not PRODUCT_URL_EXCLUDE_RE.search(path)
                ):
                    found.append(href)
            if found:
                break
        except Exception:
            continue
    return list(dict.fromkeys(found))


def _discover_from_serper(company_name: str, domain: str) -> List[str]:
    if not SERPER_API_KEY:
        return []
    try:
        resp = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={
                "q": f'site:{domain} ("advanced materials" OR "electronic materials" OR "specialty" OR products)',
                "num": 8,
            },
            timeout=15,
        )
        resp.raise_for_status()
        urls = []
        for item in resp.json().get("organic", []):
            link = item.get("link", "").split("#")[0].rstrip("/")
            if link and _is_same_domain(link, domain) and not PRODUCT_URL_EXCLUDE_RE.search(urlparse(link).path):
                urls.append(link)
        return list(dict.fromkeys(urls))
    except Exception:
        return []


def discover_product_hubs(conn, ticker: str, company_name: str) -> Tuple[Optional[str], List[str]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT domain, catalog_urls FROM company_product_sources WHERE ticker = %s;",
            (ticker,),
        )
        row = cur.fetchone()
        if row and row[1]:
            return row[0], list(row[1])

    domain = _get_company_domain(ticker)
    if not domain:
        return None, []

    urls = _discover_from_sitemap(domain)
    if len(urls) < 3:
        urls = list(dict.fromkeys(urls + _discover_from_homepage(domain)))
    if len(urls) < 2:
        urls = list(dict.fromkeys(urls + _discover_from_serper(company_name, domain)))

    # Prioritize shorter category hub URLs first, then specific product pages
    urls.sort(key=lambda u: (len(urlparse(u).path.strip("/").split("/")), len(u)))
    top_hubs = urls[:20]

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO company_product_sources (ticker, domain, catalog_urls, last_crawled_at)
            VALUES (%s, %s, %s::jsonb, NOW())
            ON CONFLICT (ticker) DO UPDATE SET
                domain = EXCLUDED.domain,
                catalog_urls = EXCLUDED.catalog_urls,
                last_crawled_at = NOW();
            """,
            (ticker, domain, json.dumps(top_hubs)),
        )
    conn.commit()
    return domain, top_hubs


def _clean_product_html(html: str, page_url: str) -> Tuple[str, List[str]]:
    soup = BeautifulSoup(html, "html.parser")
    child_links: List[str] = []
    base_domain = urlparse(page_url).netloc.lower().lstrip("www.")

    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"]).split("#")[0].rstrip("/")
        path = urlparse(href).path
        if (
            _is_same_domain(href, base_domain)
            and PRODUCT_URL_INCLUDE_RE.search(path)
            and not PRODUCT_URL_EXCLUDE_RE.search(path)
            and not href.lower().endswith((".pdf", ".jpg", ".png", ".zip"))
        ):
            child_links.append(href)

    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside", "svg"]):
        tag.decompose()

    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    blocks: List[str] = []
    for el in soup.find_all(["h1", "h2", "h3", "h4", "li", "p", "td"]):
        txt = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
        if 4 <= len(txt) <= 320:
            if el.name in ("h1", "h2", "h3", "h4"):
                blocks.append(f"## {txt}")
            else:
                blocks.append(txt)

    deduped = list(dict.fromkeys(blocks))
    clean_text = f"URL: {page_url}\nTITLE: {title}\n" + "\n".join(deduped)
    return clean_text[:4500], list(dict.fromkeys(child_links))


def crawl_company_catalog(domain: str, seed_urls: List[str]) -> str:
    visited: Set[str] = set()
    queue: List[str] = list(seed_urls)
    page_texts: List[str] = []

    while queue and len(visited) < MAX_PAGES_PER_COMPANY:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            resp = requests.get(url, headers=HTTP_HEADERS, timeout=14)
            if resp.status_code != 200 or "text/html" not in resp.headers.get("Content-Type", ""):
                continue
            cleaned, children = _clean_product_html(resp.text, url)
            if len(cleaned) > 180:
                page_texts.append(cleaned)
            for child in children:
                if child not in visited and child not in queue:
                    queue.append(child)
            time.sleep(0.35)
        except Exception:
            continue

    return "\n\n====================\n\n".join(page_texts)[:26000]


# ---------------------------------------------------------------------------
# Stage 3: Pydantic Schemas & Two-Step Extraction + Filtering
# ---------------------------------------------------------------------------

class DiscoveredCatalogProduct(BaseModel):
    raw_product_name: str = Field(
        description="Commercial product name, chemical/material grade, or brand line listed on the webpage."
    )
    canonical_material: str = Field(
        description="Matched canonical T2 material name from the provided taxonomy list, or a specific Title Case material chemistry/alloy/polymer class if novel. Never an umbrella like 'Advanced Materials'."
    )
    material_group: str = Field(
        description="Material family/group (e.g. 'Wafers and substrates', 'Semiconductor chemicals', 'Fluoropolymers', 'Battery', 'Specialty alloys', 'Technical ceramics')."
    )
    is_advanced_material: bool = Field(
        description="True ONLY if this is an engineered, high-purity, or customer-process-qualified advanced material (T2). False for commodity construction/packaging resins (standard PVC, POVAL, cement), basic industrial commodities, raw ores (T0), or assembled devices/equipment (T3)."
    )
    exclusion_reason: Optional[str] = Field(
        default=None,
        description="If is_advanced_material is False, briefly state why (e.g., 'Commodity construction resin', 'Assembled T3 component', 'Generic segment umbrella').",
    )
    performance_spec: Optional[str] = Field(
        default=None,
        description="Key technical spec or grade property from the product page (e.g., '99.9999% purity', 'Low Dk/Df dielectric', '300mm epitaxial'), or null.",
    )
    end_demand: Optional[str] = Field(
        default=None,
        description="Target downstream applications mentioned on the page (e.g., 'Semiconductors, AI Servers', 'EV Batteries, Aerospace').",
    )
    source_url: str = Field(description="Exact URL from the crawled pages where this product was found.")
    source_quote: str = Field(description="Verbatim snippet from the crawled product page naming this product.")


class CatalogAuditBatch(BaseModel):
    all_products_found: List[DiscoveredCatalogProduct] = Field(default_factory=list)


def _call_ai(prompt: str, schema) -> Optional[dict]:
    global gemini_calls_used, groq_calls_used

    if gemini_client:
        for g_model in GEMINI_MODELS:
            if g_model in exhausted_gemini_models:
                continue
            try:
                gemini_calls_used += 1
                resp = gemini_client.models.generate_content(
                    model=g_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=schema,
                        temperature=0.1,
                    ),
                )
                tok = getattr(getattr(resp, "usage_metadata", None), "total_token_count", 0) or 0
                record_ai_usage(DATABASE_URL, g_model, "Google Gemini", calls_delta=1, tokens_delta=tok)
                return json.loads(resp.text)
            except Exception as e:
                err = str(e)
                if "429" in err or "RESOURCE_EXHAUSTED" in err or "404" in err:
                    exhausted_gemini_models.add(g_model)
                    continue
                exhausted_gemini_models.add(g_model)

    if not GROQ_API_KEY:
        return None

    schema_json = json.dumps(schema.model_json_schema())
    for model_id in GROQ_MODELS:
        if model_id in exhausted_groq_models:
            continue
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": model_id,
                    "response_format": {"type": "json_object"},
                    "temperature": 0.1,
                    "messages": [
                        {
                            "role": "system",
                            "content": f"Return ONLY valid JSON matching this schema:\n{schema_json}",
                        },
                        {"role": "user", "content": prompt[:18000]},
                    ],
                },
                timeout=50,
            )
            if resp.status_code in (429, 404):
                exhausted_groq_models.add(model_id)
                continue
            resp.raise_for_status()
            data = resp.json()
            groq_calls_used += 1
            tok = data.get("usage", {}).get("total_tokens", 0) or 0
            record_ai_usage(DATABASE_URL, model_id, "Groq", calls_delta=1, tokens_delta=tok)
            time.sleep(2)
            return json.loads(data["choices"][0]["message"]["content"])
        except Exception:
            continue
    return None


def run_catalog_pipeline(conn, force_tickers: Optional[List[str]] = None, dry_run: bool = False):
    conn = ensure_schema(conn)
    alias_map, by_id = load_material_registry(conn)
    t2_vocab = ", ".join(sorted([cname for cname, tier, _ in by_id.values() if tier == "T2"]))

    with conn.cursor() as cur:
        if force_tickers:
            cur.execute(
                """
                SELECT ticker, company_name, sub_sector
                FROM equities_universe
                WHERE ticker = ANY(%s)
                ORDER BY ticker ASC;
                """,
                (force_tickers,),
            )
        else:
            cur.execute(
                """
                SELECT e.ticker, e.company_name, e.sub_sector
                FROM equities_universe e
                LEFT JOIN company_product_sources s ON s.ticker = e.ticker
                WHERE s.last_crawled_at IS NULL
                ORDER BY e.market_cap_usd DESC NULLS LAST, e.ticker ASC
                LIMIT %s;
                """,
                (BATCH_LIMIT,),
            )
        targets = cur.fetchall()

    print(f"Crawling official product catalogs for {len(targets)} companies...", flush=True)
    stats = Counter()

    for idx, (ticker, company_name, sub_sector) in enumerate(targets, start=1):
        print(f"\n[{idx}/{len(targets)}] {ticker} ({company_name})", flush=True)
        domain, hubs = discover_product_hubs(conn, ticker, company_name)
        if not domain or not hubs:
            print(f"  No product catalog URLs discovered (domain={domain}).", flush=True)
            stats["no_catalog"] += 1
            continue

        print(f"  Domain: {domain} | Seed hubs: {len(hubs)} (top: {hubs[0]})", flush=True)
        crawled_corpus = crawl_company_catalog(domain, hubs)
        if len(crawled_corpus) < 250:
            print("  Crawled pages yielded insufficient product text.", flush=True)
            stats["empty_crawl"] += 1
            continue

        prompt = f"""
        You are an institutional advanced materials analyst auditing the official product catalog pages of {company_name} ({ticker}) | Sub-sector: {sub_sector}.

        TASK — TWO-STEP CATALOG AUDIT:
        1. Scan ALL crawled pages below and list EVERY commercial product, chemical, polymer, alloy, ceramic, wafer, or material line that {company_name} sells (`all_products_found`).
        2. For each product, strictly evaluate `is_advanced_material`:
           - Set `is_advanced_material = true` ONLY if it is a **Tier-2 (T2) engineered advanced material**—customer-process-qualified, high-purity, or specialty performance material (e.g., semiconductor wafers, photoresists, CMP slurries, specialty silicones, fluoropolymers, carbon fiber prepregs, cathode/anode battery materials, NdFeB magnets, titanium/nickel superalloys, optical crystals, copper clad laminates).
           - Set `is_advanced_material = false` for:
             * Commodity construction, infrastructure, or bulk packaging materials (e.g., standard PVC pipe resin, POVAL/polyvinyl alcohol, cement, basic caustic soda/chlor-alkali, commodity polyethylene) unless explicitly high-purity electronic/battery grade.
             * Raw ores/metals or unrefined feedstocks (T0/T1 like raw silicon metal, copper ore, bulk methanol).
             * Finished downstream assemblies, sensors, connectors, batteries, displays, or machinery (T3).
             * Generic category headers (`"Advanced Materials"`, `"Electronic Specialty and Advanced Materials"`).
        3. Match `canonical_material` to an EXACT name from this T2 taxonomy list whenever applicable:
           [{t2_vocab}]
           If genuinely novel, provide a specific Title Case chemical/material class name (never a vague umbrella).
        4. Emit ONE entry per canonical material class (combine multiple grades or end-markets into `end_demand` and `performance_spec`).

        CRAWLED PRODUCT CATALOG PAGES:
        {crawled_corpus}
        """

        parsed = _call_ai(prompt, CatalogAuditBatch)
        if not parsed:
            print("  AI providers exhausted — stopping.", flush=True)
            break

        all_items = parsed.get("all_products_found", [])
        kept_by_mid: Dict[int, dict] = {}

        print(f"  Total catalog items scanned: {len(all_items)}", flush=True)
        for item in all_items:
            raw_name = (item.get("raw_product_name") or "").strip()
            canon_label = (item.get("canonical_material") or "").strip()
            is_adv = bool(item.get("is_advanced_material"))

            if (
                not is_adv
                or not canon_label
                or VAGUE_UMBRELLA_PRODUCT_RE.match(canon_label)
                or VAGUE_UMBRELLA_PRODUCT_RE.match(raw_name)
                or BARE_T0_T1_NAMES_RE.match(canon_label)
            ):
                stats["filtered_out"] += 1
                print(
                    f"    [FILTERED OUT] {raw_name or canon_label} -> {item.get('exclusion_reason') or 'Non-T2 / Umbrella'}",
                    flush=True,
                )
                continue

            resolved = mint_or_get_t2_material(
                conn,
                alias_map,
                by_id,
                canon_label,
                item.get("material_group") or "Engineered Materials",
            )
            if not resolved:
                continue

            mid, cname, db_tier = resolved
            if db_tier != "T2":
                stats["filtered_out"] += 1
                continue

            clean_prod = raw_name if raw_name and not VAGUE_UMBRELLA_PRODUCT_RE.match(raw_name) else cname
            print(f"    [KEPT T2] {clean_prod} (canonical: {cname})", flush=True)

            if mid not in kept_by_mid:
                kept_by_mid[mid] = {
                    "canonical_name": cname,
                    "product_name": clean_prod[:160],
                    "performance_spec": item.get("performance_spec"),
                    "end_demand": item.get("end_demand"),
                    "source_url": (item.get("source_url") or hubs[0])[:200],
                    "source_quote": (item.get("source_quote") or clean_prod)[:400],
                }
            else:
                # Merge end-demands if multiple grades map to the same canonical T2 material
                existing_ed = kept_by_mid[mid].get("end_demand") or ""
                new_ed = item.get("end_demand") or ""
                if new_ed and new_ed.lower() not in existing_ed.lower():
                    kept_by_mid[mid]["end_demand"] = f"{existing_ed}, {new_ed}".strip(", ")[:240]

        if dry_run:
            stats["dry_run_kept"] += len(kept_by_mid)
            continue

        link_rows = [
            (
                ticker,
                mid,
                "sells",
                meta["product_name"],
                sub_sector or "Advanced Materials",
                meta["performance_spec"],
                meta["end_demand"],
                f"Product Catalog ({meta['source_url']})",
                meta["source_quote"],
                "high",
            )
            for mid, meta in kept_by_mid.items()
        ]

        with conn.cursor() as cur:
            if link_rows:
                cur.execute("DELETE FROM company_material_links WHERE ticker = %s AND role = 'sells';", (ticker,))
                cur.executemany(
                    """
                    INSERT INTO company_material_links (
                        ticker, material_id, role, product_name, business_unit,
                        performance_spec, end_demand, source_section, source_quote, confidence
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ticker, material_id, role, COALESCE(product_name, '')) DO UPDATE SET
                        business_unit = EXCLUDED.business_unit,
                        performance_spec = COALESCE(EXCLUDED.performance_spec, company_material_links.performance_spec),
                        end_demand = COALESCE(EXCLUDED.end_demand, company_material_links.end_demand),
                        source_section = EXCLUDED.source_section,
                        source_quote = EXCLUDED.source_quote,
                        confidence = 'high',
                        updated_at = NOW();
                    """,
                    link_rows,
                )
            cur.execute(
                "UPDATE company_product_sources SET last_crawled_at = NOW() WHERE ticker = %s;",
                (ticker,),
            )
        conn.commit()
        stats["saved_t2"] += len(link_rows)

    print(
        f"\nCatalog crawl complete (Gemini calls={gemini_calls_used}, Groq calls={groq_calls_used}). "
        f"Stats: {dict(stats)}",
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Discover and crawl official advanced materials product pages.")
    parser.add_argument("--tickers", type=str, default="", help="Comma-separated tickers (e.g., 4063.T,ENTG,ALB).")
    parser.add_argument("--dry-run", action="store_true", help="Print discovered & filtered products without writing to DB.")
    args = parser.parse_args()

    conn = connect_db()
    try:
        forced = [t.strip().upper() for t in args.tickers.split(",") if t.strip()] or None
        run_catalog_pipeline(conn, force_tickers=forced, dry_run=args.dry_run)
    finally:
        if conn and not conn.closed:
            conn.close()
