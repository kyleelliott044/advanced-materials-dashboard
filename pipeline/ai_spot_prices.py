import os
import re
import json
import psycopg
from datetime import date
from pydantic import BaseModel, Field
from typing import List, Optional
from google import genai
from google.genai import types

DATABASE_URL = os.environ["DATABASE_URL"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

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


class MaterialSpotQuote(BaseModel):
    input_code: str = Field(description="Exact code from the requested list.")
    price_usd: Optional[float] = Field(description="Latest spot/assessment price converted to USD, or null if not reliably found.")
    observation_date: str = Field(description="YYYY-MM-DD date of the price quote.")
    source_name: str = Field(description="Publication, exchange, or agency name (e.g., SMM, Fastmarkets, USGS, Asian Metal, GFEX).")
    confidence: str = Field(description="'high', 'medium', or 'low'.")


class MaterialSpotBatch(BaseModel):
    quotes: List[MaterialSpotQuote]


def refresh_ai_material_prices(conn):
    if not client:
        print("GEMINI_API_KEY not set — skipping AI spot price refresh.")
        return

    # Preserve daily Gemini quota if AI spot prices were refreshed within the last 3 days
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(observation_date) FROM raw_inputs WHERE is_proxy = TRUE;")
        row = cur.fetchone()
        if row and row[0] and (date.today() - row[0]).days < 3:
            print(f"AI spot prices are current (last updated {row[0]}) — skipping to preserve Gemini quota.")
            return

    today_str = date.today().isoformat()
    mat_list = "\n".join([f"- {code}: {name} (target unit: {unit})" for code, name, unit, _ in SPECIALTY_MATERIALS])
    meta_map = {code: (name, unit, cat) for code, name, unit, cat in SPECIALTY_MATERIALS}

    prompt = f"""
    Today is {today_str}. Search for the most recent spot or market assessment prices (converted to USD in the exact target unit requested) for these non-exchange specialty materials:

    {mat_list}

    Rules:
    - Convert RMB/CNY quotes to USD using current FX rates if quoted on Chinese spot markets (SMM, Asian Metal, GFEX).
    - Do NOT guess or fabricate prices. If no recent quote (within the last 45 days) is verifiable in search results, set price_usd to null.
    """

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
    except Exception as e:
        err = str(e)
        if "429" in err or "RESOURCE_EXHAUSTED" in err:
            print("Gemini daily quota reached — skipping AI spot price refresh for this run.")
            return
        if any(k in err for k in ("503", "UNAVAILABLE", "disconnected", "timed out")):
            print("Gemini temporarily unavailable — skipping AI spot price refresh for this run.")
            return
        print(f"Warning during AI spot price search: {e} — continuing pipeline.")
        return

    # Extract grounding URLs returned by Google Search for auditability
    grounding_urls = []
    try:
        chunks = resp.candidates[0].grounding_metadata.grounding_chunks or []
        grounding_urls = [c.web.uri for c in chunks if getattr(c, "web", None) and c.web.uri][:5]
    except Exception:
        pass

    raw_text = resp.text or ""
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
            note = f"AI-Grounded Spot ({q.get('source_name', 'Market Assessment')}) | Refs: {', '.join(grounding_urls[:2])}"

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
    print(f"Upserted {rows_upserted} AI-grounded specialty material prices.")


if __name__ == "__main__":
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        refresh_ai_material_prices(conn)
