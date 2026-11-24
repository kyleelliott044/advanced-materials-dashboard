import os
import re
import time
import json
import warnings
from datetime import date
from typing import List, Optional
from urllib.parse import quote_plus
import requests
import psycopg
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

DATABASE_URL = os.environ["DATABASE_URL"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

GROQ_MODELS = [
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-20b",
]

SPECIALTY_MATERIALS = [
    ("NDPR_CN", "Neodymium-Praseodymium (NdPr) Oxide Spot", "USD / kg", "Rare Earths"),
    ("LICARB_CN", "Battery-Grade Lithium Carbonate (99.5% Spot)", "USD / Metric Ton", "Battery Materials"),
    ("GALLIUM_9999", "Refined Gallium Metal (99.99% Spot)", "USD / kg", "Critical Semicon Metals"),
    ("GERMANIUM_INGOT", "Germanium Ingot (50 Ohm-cm Spot)", "USD / kg", "Critical Semicon Metals"),
    ("POLYSILICON_N", "Solar/Semicon N-Type Polysilicon Spot", "USD / kg", "Semicon & Solar"),
    ("SPHERICAL_GRAPHITE", "Uncoated Spherical Graphite (99.95% Spot)", "USD / Metric Ton", "Battery Materials"),
    ("COBALT_SULFATE", "Battery-Grade Cobalt Sulfate Spot", "USD / Metric Ton", "Battery & Alloy Metal"),
    ("CARBON_FIBER_T700", "Industrial Standard-Modulus Carbon Fiber Tow", "USD / kg", "Composites"),
]

SEARCH_QUERIES = {
    "NDPR_CN": ["NdPr oxide price per kg SMM", "praseodymium neodymium oxide spot price"],
    "LICARB_CN": ["battery grade lithium carbonate price per ton SMM GFEX", "lithium carbonate spot price yuan ton"],
    "GALLIUM_9999": ["gallium metal price per kg spot", "refined gallium 99.99 price USD kg"],
    "GERMANIUM_INGOT": ["germanium ingot price per kg spot", "germanium metal price USD kg"],
    "POLYSILICON_N": ["n-type polysilicon price per kg InfoLink", "polysilicon spot price USD kg"],
    "SPHERICAL_GRAPHITE": ["spherical graphite price per ton", "uncoated spherical graphite spot price"],
    "COBALT_SULFATE": ["cobalt sulfate price per ton SMM", "battery grade cobalt sulfate spot price"],
    "CARBON_FIBER_T700": ["carbon fiber T700 price per kg", "industrial carbon fiber tow price USD kg"],
}


class MaterialSpotQuote(BaseModel):
    input_code: str = Field(description="Exact code from the requested list.")
    price_usd: Optional[float] = Field(description="Latest spot/assessment price converted to USD, or null if not reliably found.")
    observation_date: str = Field(description="YYYY-MM-DD date of the price quote.")
    source_name: str = Field(description="Publication, exchange, or agency name (e.g., SMM, Fastmarkets, USGS, Asian Metal, GFEX).")
    confidence: str = Field(description="'high', 'medium', or 'low'.")


class MaterialSpotBatch(BaseModel):
    quotes: List[MaterialSpotQuote]


def _fetch_rss_snippets(target_codes: list[str]) -> tuple[str, list[str]]:
    """Fetches public Google News & Bing News RSS items for missing/stale materials."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; MaterialsMacroBot/1.0)"}
    snippets = []
    urls = []
    seen_titles = set()

    for code in target_codes:
        for query in SEARCH_QUERIES.get(code, []):
            encoded = quote_plus(query)
            rss_urls = [
                f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en",
                f"https://www.bing.com/news/search?q={encoded}&format=rss",
            ]
            for rss_url in rss_urls:
                try:
                    r = requests.get(rss_url, headers=headers, timeout=12)
                    if r.status_code != 200:
                        continue
                    soup = BeautifulSoup(r.text, "html.parser")
                    for item in soup.find_all("item")[:3]:
                        title = item.find("title").get_text(strip=True) if item.find("title") else ""
                        if not title or title in seen_titles:
                            continue
                        seen_titles.add(title)
                        pub_date = item.find("pubdate").get_text(strip=True) if item.find("pubdate") else ""
                        desc_raw = item.find("description").get_text(" ", strip=True) if item.find("description") else ""
                        desc_clean = BeautifulSoup(desc_raw, "html.parser").get_text(" ", strip=True)[:240]
                        snippets.append(f"[{code}] ({pub_date}) {title} — {desc_clean}")
                        if len(urls) < 4:
                            urls.append(rss_url.split("?")[0])
                except Exception:
                    continue
            time.sleep(0.15)

    print(f"Retrieved {len(snippets)} RSS market snippets across {len(target_codes)} materials.")
    return "\n".join(snippets), urls


def _search_via_groq(prompt: str, target_codes: list[str]) -> tuple[str, list[str]]:
    if not GROQ_API_KEY:
        return "", []
    snippets, urls = _fetch_rss_snippets(target_codes)
    if not snippets:
        return "", []

    schema_hint = json.dumps(MaterialSpotBatch.model_json_schema())
    for model_id in GROQ_MODELS:
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
                                "You are a commodities data analyst. Extract verifiable spot prices from the provided "
                                "market news snippets and return ONLY a valid JSON object matching this exact schema:\n"
                                f"{schema_hint}"
                            ),
                        },
                        {
                            "role": "user",
                            "content": f"{prompt}\n\nMARKET RSS SNIPPETS:\n{snippets[:14000]}",
                        },
                    ],
                },
                timeout=45,
            )
            if resp.status_code == 429:
                print(f"Groq rate limit on {model_id} — trying next model.")
                continue
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"] or "", urls
        except Exception as e:
            print(f"Groq spot-price extraction failed on {model_id}: {e}")
            continue

    return "", []


def refresh_ai_material_prices(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT input_code, MAX(observation_date)
            FROM raw_inputs
            WHERE is_proxy = TRUE
            GROUP BY input_code;
            """
        )
        last_updated = {r[0]: r[1] for r in cur.fetchall()}

    stale_materials = [
        item for item in SPECIALTY_MATERIALS
        if item[0] not in last_updated or (date.today() - last_updated[item[0]]).days >= 3
    ]

    if not stale_materials:
        print("All AI spot prices are current (< 3 days old) — skipping.")
        return

    today_str = date.today().isoformat()
    stale_codes = [m[0] for m in stale_materials]
    mat_list = "\n".join([f"- {code}: {name} (target unit: {unit})" for code, name, unit, _ in stale_materials])
    meta_map = {code: (name, unit, cat) for code, name, unit, cat in stale_materials}

    prompt = f"""
    Today is {today_str}. Extract the most recent spot or market assessment prices (converted to USD in the exact target unit requested) for these non-exchange specialty materials:

    {mat_list}

    Rules:
    - Convert RMB/CNY quotes to USD (~7.2 RMB/USD) if quoted on Chinese spot markets (SMM, Asian Metal, GFEX).
    - Convert per-ton quotes to per-kg (divide by 1,000) or per-kg to per-metric-ton (multiply by 1,000) to match the exact target unit.
    - Do NOT guess or fabricate prices. If no recent quote is verifiable in the results, set price_usd to null.
    """

    raw_text = ""
    grounding_urls = []
    provider_label = "Gemini Search"

    if client:
        try:
            resp = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    response_mime_type="application/json",
                    response_schema=MaterialSpotBatch,
                    temperature=0.1,
                ),
            )
            raw_text = resp.text or ""
            chunks = resp.candidates[0].grounding_metadata.grounding_chunks or []
            grounding_urls = [c.web.uri for c in chunks if getattr(c, "web", None) and c.web.uri][:5]
        except Exception as e:
            print(f"Gemini search unavailable ({e}) — falling back to RSS + Groq.")

    if not raw_text:
        provider_label = "Market RSS + Groq"
        raw_text, grounding_urls = _search_via_groq(prompt, stale_codes)

    json_match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if not json_match:
        print("No valid JSON returned from AI spot price search.")
        return

    parsed = json.loads(json_match.group(0))
    rows_upserted = 0

    with conn.cursor() as cur:
        for q in parsed.get("quotes", []):
            code = q.get("input_code")
            price = q.get("price_usd")
            if code not in meta_map or price is None or price <= 0:
                continue
            if q.get("confidence") == "low":
                continue

            name, unit, category = meta_map[code]
            obs_date = q.get("observation_date") or today_str
            refs = f" | Refs: {', '.join(grounding_urls[:2])}" if grounding_urls else ""
            note = f"{provider_label} ({q.get('source_name', 'Market Assessment')}){refs}"

            cur.execute(
                """
                INSERT INTO raw_inputs (input_code, input_name, category, unit, observation_date, value, is_proxy, source_note)
                VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s)
                ON CONFLICT (input_code, observation_date) DO UPDATE SET
                    value = EXCLUDED.value,
                    is_proxy = TRUE,
                    source_note = EXCLUDED.source_note;
                """,
                (code, name, category, unit, obs_date, round(float(price), 2), note),
            )
            rows_upserted += 1

    conn.commit()
    print(f"Upserted {rows_upserted} AI-grounded specialty material prices via {provider_label}.")


if __name__ == "__main__":
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        refresh_ai_material_prices(conn)
