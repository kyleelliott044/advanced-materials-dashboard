import os
import re
import time
import json
from datetime import date
from typing import List, Optional
from urllib.parse import quote_plus
import requests
import psycopg
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

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

SEARCH_QUERIES = [
    ("NDPR_CN", "NdPr oxide price USD kg rare earth"),
    ("LICARB_CN", "lithium carbonate price ton SMM GFEX"),
    ("GALLIUM_9999", "gallium metal price kg spot"),
    ("GERMANIUM_INGOT", "germanium ingot price kg spot"),
    ("POLYSILICON_N", "n-type polysilicon price kg"),
    ("SPHERICAL_GRAPHITE", "spherical graphite price ton"),
    ("COBALT_SULFATE", "cobalt sulfate price ton"),
    ("CARBON_FIBER_T700", "carbon fiber price kg aerospace industrial"),
]


class MaterialSpotQuote(BaseModel):
    input_code: str = Field(description="Exact code from the requested list.")
    price_usd: Optional[float] = Field(description="Latest spot/assessment price converted to USD, or null if not reliably found.")
    observation_date: str = Field(description="YYYY-MM-DD date of the price quote.")
    source_name: str = Field(description="Publication, exchange, or agency name (e.g., SMM, Fastmarkets, USGS, Asian Metal, GFEX).")
    confidence: str = Field(description="'high', 'medium', or 'low'.")


class MaterialSpotBatch(BaseModel):
    quotes: List[MaterialSpotQuote]


def _fetch_rss_snippets() -> tuple[str, list[str]]:
    """Fetches public Google News & Bing News RSS items (unblocked on GitHub Actions)."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; MaterialsMacroBot/1.0)"}
    snippets = []
    urls = []
    seen_titles = set()

    for code, query in SEARCH_QUERIES:
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
                    desc_clean = BeautifulSoup(desc_raw, "html.parser").get_text(" ", strip=True)[:220]
                    snippets.append(f"[{code}] ({pub_date}) {title} — {desc_clean}")
                    if len(urls) < 4:
                        urls.append(rss_url.split("?")[0])
            except Exception:
                continue
        time.sleep(0.2)

    print(f"Retrieved {len(snippets)} RSS market snippets across {len(SEARCH_QUERIES)} materials.")
    return "\n".join(snippets), urls


def _search_via_groq(prompt: str) -> tuple[str, list[str]]:
    if not GROQ_API_KEY:
        return "", []
    snippets, urls = _fetch_rss_snippets()
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
        cur.execute("SELECT MAX(observation_date) FROM raw_inputs WHERE is_proxy = TRUE;")
        row = cur.fetchone()
        if row and row[0] and (date.today() - row[0]).days < 3:
            print(f"AI spot prices are current (last updated {row[0]}) — skipping.")
            return

    today_str = date.today().isoformat()
    mat_list = "\n".join([f"- {code}: {name} (target unit: {unit})" for code, name, unit, _ in SPECIALTY_MATERIALS])
    meta_map = {code: (name, unit, cat) for code, name, unit, cat in SPECIALTY_MATERIALS}

    prompt = f"""
    Today is {today_str}. Extract the most recent spot or market assessment prices (converted to USD in the exact target unit requested) for these non-exchange specialty materials:

    {mat_list}

    Rules:
    - Convert RMB/CNY quotes to USD (~7.2 RMB/USD) if quoted on Chinese spot markets (SMM, Asian Metal, GFEX).
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
        raw_text, grounding_urls = _search_via_groq(prompt)

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
