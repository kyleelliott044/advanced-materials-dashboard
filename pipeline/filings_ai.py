import os
import re
import time
import json
from datetime import date
from typing import List, Optional
import requests
import psycopg
import yfinance as yf
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

DATABASE_URL = os.environ["DATABASE_URL"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

SEC_HEADERS = {
    "User-Agent": "AdvancedMaterialsResearchPlatform research@materials-macro.io",
    "Accept-Encoding": "gzip, deflate",
}

client = genai.Client(api_key=GEMINI_API_KEY)

MAX_REQUESTS_PER_RUN = 18
requests_used = 0
groq_requests_used = 0
quota_exhausted = False
consecutive_gemini_503 = 0

GROQ_MODELS = [
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-20b",
]
exhausted_groq_models = set()

# Canonical mapping so AI-extracted commodities link to dashboard filter cards
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
    print("Reconnecting to Neon PostgreSQL...")
    return connect_db()


class _FallbackResp:
    def __init__(self, text: str):
        self.text = text


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


def _call_groq_fallback(prompt: str, schema):
    global groq_requests_used
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
                        "temperature": 0.2,
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "You are an institutional equity analyst. "
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
                print(f"Groq network error on {model_id}: {e} — trying next model.")
                break

            if resp.status_code == 429:
                err_lower = resp.text.lower()
                if "tokens per day" in err_lower or "requests per day" in err_lower:
                    print(f"Groq daily limit reached for {model_id} — switching to next model.")
                    exhausted_groq_models.add(model_id)
                    break
                if attempt == 0:
                    print(f"Groq TPM limit on {model_id} — waiting 15s...")
                    time.sleep(15)
                    continue
                print(f"Groq still rate-limited on {model_id} — trying next model.")
                break

            if resp.status_code in (400, 404, 413):
                print(f"Groq {resp.status_code} on {model_id} — switching to next model.")
                if resp.status_code == 404:
                    exhausted_groq_models.add(model_id)
                break

            resp.raise_for_status()
            groq_requests_used += 1
            content = resp.json()["choices"][0]["message"]["content"]
            time.sleep(3)
            return _FallbackResp(content)

    return None


def call_gemini(prompt: str, schema, retries: int = 2):
    global requests_used, quota_exhausted, consecutive_gemini_503

    if not quota_exhausted and consecutive_gemini_503 < 2 and requests_used < MAX_REQUESTS_PER_RUN:
        for attempt in range(retries):
            try:
                requests_used += 1
                resp = client.models.generate_content(
                    model="gemini-3.8-flash",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=schema,
                    ),
                )
                consecutive_gemini_503 = 0
                return resp
            except Exception as e:
                err = str(e)
                if "429" in err or "RESOURCE_EXHAUSTED" in err:
                    quota_exhausted = True
                    print("Daily Gemini quota reached — switching to Groq fallback.")
                    break
                if any(k in err for k in ("503", "UNAVAILABLE", "disconnected", "timed out")):
                    if attempt < retries - 1:
                        print("Gemini 503/transient error — retrying in 3s...")
                        time.sleep(3)
                        continue
                    consecutive_gemini_503 += 1
                    print("Gemini unavailable — switching to Groq fallback.")
                    break
                raise

    return _call_groq_fallback(prompt, schema)


# Sub-sector fallback sensitivity map
SENSITIVITY_RULES = [
    ("Industrial Gases", "NG=F", "Cost Input", "Pass-Through", "Natural gas is primary feedstock for hydrogen/syngas; indexed via take-or-pay pass-through clauses."),
    ("Industrial Gases", "INDPRO", "Revenue Proxy", "Direct (Demand)", "Base merchant gas volumes track industrial manufacturing utilization."),
    ("Specialty Alloys", "ITA", "Revenue Proxy", "Direct (Demand)", "Commercial jet engine & airframe build rates drive multi-year vacuum-melted alloy backlogs."),
    ("Specialty Alloys", "WPU101", "Cost Input", "Pass-Through", "Nickel, cobalt, and titanium scrap surcharges pass through with a 1-2 quarter lag."),
    ("Specialty Alloys", "URA", "Revenue Proxy", "Direct (Demand)", "Nuclear fuel cladding and SMR zirconium/nickel alloy demand."),
    ("Specialty Alloys", "PNICKUSDM", "Cost Input", "Pass-Through", "Nickel is a core alloying element; surcharges pass through with a 1-2 quarter lag."),
    ("Specialty Alloys", "PCOBAUSDM", "Cost Input", "Pass-Through", "Cobalt superalloy melt costs indexed to aerospace OEM contracts."),
    ("Aerospace & Defense Parts", "ITA", "Revenue Proxy", "Direct (Demand)", "Aftermarket PMA parts and defense spares scale with global flight hours."),
    ("Beryllium & Specialty Alloys", "SMH", "Revenue Proxy", "Direct (Demand)", "Tantalum/precious metal targets and beryllium optics tied to wafer fab & defense spend."),
    ("Aerospace & Auto Aluminum", "ALI=F", "Cost Input", "Pass-Through", "LME aluminum price passes through; conversion spreads depend on aerospace plate tightness."),
    ("Titanium Sponge & Metal", "ITA", "Revenue Proxy", "Direct (Demand)", "Aerospace-grade titanium sponge supply tightness drives multi-year OEM contract pricing."),
    ("Titanium Metal & Powders", "ITA", "Revenue Proxy", "Direct (Demand)", "Additive manufacturing titanium powders for defense and aerospace."),
    ("Stainless & Specialty Steel", "SLX", "Revenue Proxy", "Direct (Demand)", "Benchmark steel/alloy pricing sets base conversion margins."),
    ("Stainless & High-Perf Alloys", "HG=F", "Revenue Proxy", "Direct (Demand)", "Electrification, LNG, and chemical processing capex drive high-nickel alloy orders."),
    ("Advanced Stainless & Heating Alloys", "HG=F", "Revenue Proxy", "Direct (Demand)", "Industrial electric heating elements benefit from industrial decarbonization."),
    ("Specialty Steel & Alloys", "SLX", "Revenue Proxy", "Direct (Demand)", "Clean bar and seamless mechanical tubing tied to industrial/defense capex."),
    ("Semiconductor Materials", "SMH", "Revenue Proxy", "Direct (Demand)", "Wafer starts and logic/memory node transitions drive filtration and deposition chemical consumption."),
    ("Silicon Wafers", "SMH", "Revenue Proxy", "Direct (Demand)", "300mm wafer shipments governed by long-term LTA take-or-pay volume commitments."),
    ("Silicon Wafers", "POLYSILICON_N", "Cost Input", "Pass-Through", "Electronic-grade polysilicon feedstock passes through under multi-year wafer LTAs."),
    ("Silicon Wafers & PVC", "SMH", "Revenue Proxy", "Direct (Demand)", "300mm silicon wafer utilization tracks global fab output; PVC tied to construction/rates."),
    ("Silicon Wafers & PVC", "POLYSILICON_N", "Cost Input", "Pass-Through", "Polysilicon spot trends signal upstream silicon supply-demand tightness."),
    ("Engineered SOI Wafers", "SMH", "Revenue Proxy", "Direct (Demand)", "RF-SOI and FD-SOI wafers scale with 5G smartphone RF content and edge AI."),
    ("Photoresists & Semicon Chem", "SMH", "Revenue Proxy", "Direct (Demand)", "EUV and ArF immersion photoresists scale directly with leading-edge lithography layers."),
    ("Semicon Packaging & Chemicals", "SMH", "Revenue Proxy", "Direct (Demand)", "HBM and advanced 2.5D/3D packaging epoxy/chemical demand."),
    ("Semicon Packaging & Chemicals", "SI=F", "Cost Input", "Pass-Through", "Silver conductive pastes and plating chemistry for chip packaging."),
    ("IC Substrates & Ceramics", "SMH", "Revenue Proxy", "Direct (Demand)", "AI server GPU package substrates (ABF) drive high-margin mix expansion."),
    ("ABF Build-Up Film & Bio", "SMH", "Revenue Proxy", "Direct (Demand)", "Near-monopoly on Ajinomoto Build-up Film insulating layers for AI accelerators."),
    ("CMP Slurries & Wet Chemicals", "SMH", "Revenue Proxy", "Direct (Demand)", "More CMP planarization steps per wafer in 3D NAND and gate-all-around logic."),
    ("CMP Slurries & Abrasives", "SMH", "Revenue Proxy", "Direct (Demand)", "Ultra-pure colloidal silica and silicon carbide wafer polishing slurries."),
    ("Silicon Carbide Wafers", "LIT", "Revenue Proxy", "Direct (Demand)", "800V EV traction inverters and AI data center power supplies drive 200mm SiC substrate demand."),
    ("Compound Semicon & Photonics", "SMH", "Revenue Proxy", "Direct (Demand)", "800G/1.6T optical transceivers for AI data center clusters."),
    ("Compound Semicon & Photonics", "GALLIUM_9999", "Cost Input", "Inverse (Cost)", "Refined gallium is the core substrate feedstock for GaAs/GaN RF and optical wafers."),
    ("Compound Semicon & Photonics", "GERMANIUM_INGOT", "Cost Input", "Inverse (Cost)", "Germanium substrates are required for space solar cells and IR/optical photonics."),
    ("High-Speed CCL (AI Servers)", "HG=F", "Cost Input", "Pass-Through", "Copper foil and glass fabric costs pass through; ultra-low-loss M7/M8 resin grades drive AI server margins."),
    ("High-Frequency CCL", "HG=F", "Cost Input", "Pass-Through", "High-speed networking switch and AI accelerator PCB laminate demand."),
    ("Copper Clad Laminates", "HG=F", "Cost Input", "Pass-Through", "Copper foil price swings directly impact working capital and pass-through pricing."),
    ("Rare Earth Mining & Magnets", "REMX", "Revenue Proxy", "Direct (Demand)", "NdPr oxide pricing dictates mining EBITDA; EV traction motors and defense actuators drive magnet demand."),
    ("Rare Earth Mining & Magnets", "NDPR_CN", "Revenue Proxy", "Direct (Demand)", "NdPr oxide spot pricing directly governs rare earth separation and magnet margins."),
    ("Rare Earth Oxides & Metals", "REMX", "Revenue Proxy", "Direct (Demand)", "Chinese quota allocations and NdPr spot prices govern upstream margins."),
    ("Rare Earth Oxides & Metals", "NDPR_CN", "Revenue Proxy", "Direct (Demand)", "Spot NdPr oxide benchmarks dictate upstream oxide realization."),
    ("NdFeB Permanent Magnets", "REMX", "Cost Input", "Pass-Through", "NdPr metal cost passes through to EV/wind OEMs with value-add processing fee."),
    ("NdFeB Permanent Magnets", "NDPR_CN", "Cost Input", "Pass-Through", "NdPr metal feedstock passes through to motor OEMs with a quarterly lag."),
    ("Lithium Chemicals", "LIT", "Revenue Proxy", "Direct (Demand)", "Lithium carbonate/hydroxide spot prices directly drive brine and spodumene conversion margins."),
    ("Lithium Chemicals", "LICARB_CN", "Revenue Proxy", "Direct (Demand)", "Battery-grade lithium carbonate spot prices drive brine and converter EBITDA."),
    ("Lithium Refining & Batteries", "LIT", "Revenue Proxy", "Direct (Demand)", "Spodumene-to-hydroxide conversion spread determines refining profitability."),
    ("Lithium Refining & Batteries", "LICARB_CN", "Revenue Proxy", "Direct (Demand)", "Spot lithium carbonate/hydroxide spreads set refining conversion margins."),
    ("High-Nickel Cathodes", "LIT", "Cost Input", "Pass-Through", "Lithium and nickel metal prices are indexed to cell-maker contracts; processing margin is fixed per kg."),
    ("High-Nickel Cathodes", "PNICKUSDM", "Cost Input", "Pass-Through", "Nickel sulfate costs pass through to battery cell makers."),
    ("High-Nickel Cathodes", "PCOBAUSDM", "Cost Input", "Pass-Through", "Cobalt feedstock indexed in NMC cathode contracts."),
    ("High-Nickel Cathodes", "LICARB_CN", "Cost Input", "Pass-Through", "Lithium carbonate spot indices pass through in cathode tolling agreements."),
    ("High-Nickel Cathodes", "COBALT_SULFATE", "Cost Input", "Pass-Through", "Battery-grade cobalt sulfate costs are indexed to cell-maker contracts."),
    ("Cathodes & Synthetic Anodes", "LIT", "Cost Input", "Pass-Through", "Lithium pass-through pricing paired with IRA-compliant North American supply chain premiums."),
    ("Cathodes & Synthetic Anodes", "LICARB_CN", "Cost Input", "Pass-Through", "Lithium feedstock cost passes through with IRA-compliant processing spreads."),
    ("Battery Electrolytes", "LIT", "Revenue Proxy", "Direct (Demand)", "LiPF6 salt spreads and global GWh battery cell production."),
    ("Battery Electrolytes", "LICARB_CN", "Cost Input", "Pass-Through", "Lithium carbonate is the primary precursor for LiPF6 electrolyte salts."),
    ("Elecfoil (Battery Copper Foil)", "HG=F", "Cost Input", "Pass-Through", "LME copper passes through; ultra-thin 4-6 micron foil processing fee drives margin."),
    ("Natural Graphite Anode Mat.", "LIT", "Revenue Proxy", "Direct (Demand)", "Ex-China spherical graphite qualification for North American EV anode supply."),
    ("Natural Graphite Anode Mat.", "SPHERICAL_GRAPHITE", "Revenue Proxy", "Direct (Demand)", "Uncoated spherical graphite spot pricing benchmarks ex-China anode economics."),
    ("Synthetic Graphite Anodes", "LIT", "Revenue Proxy", "Direct (Demand)", "Graphitization energy costs and IRA-compliant anode localization."),
    ("Synthetic Graphite Anodes", "SPHERICAL_GRAPHITE", "Revenue Proxy", "Direct (Demand)", "Spherical graphite spot benchmarks compete directly with synthetic anode pricing."),
    ("Aerospace Carbon Fiber Composites", "ITA", "Revenue Proxy", "Direct (Demand)", "Widebody (A350/787) and defense composite weight-per-aircraft drives multi-year volume."),
    ("Aerospace Carbon Fiber Composites", "NG=F", "Cost Input", "Inverse (Cost)", "PAN precursor carbonization ovens are energy-intensive; natural gas spikes compress margins."),
    ("Aerospace Carbon Fiber Composites", "CARBON_FIBER_T700", "Revenue Proxy", "Direct (Demand)", "Industrial carbon fiber tow pricing tracks global composite utilization."),
    ("Carbon Fiber & Advanced Films", "ITA", "Revenue Proxy", "Direct (Demand)", "Aerospace prepreg and pressure vessel tow demand."),
    ("Carbon Fiber & Advanced Films", "CARBON_FIBER_T700", "Revenue Proxy", "Direct (Demand)", "Standard-modulus carbon fiber spot rates reflect wind and pressure-vessel demand."),
    ("Optical Fiber & Display Glass", "SMH", "Revenue Proxy", "Direct (Demand)", "AI data center high-density fiber interconnects and Gen 10.5 display glass utilization."),
    ("Optical Fiber & Display Glass", "PL=F", "Cost Input", "Inverse (Cost)", "Platinum-rhodium bushings are required for high-temp specialty glass drawing."),
    ("Optical Fiber & Display Glass", "GERMANIUM_INGOT", "Cost Input", "Inverse (Cost)", "Germanium tetrachloride is a critical core dopant in telecom optical fiber preforms."),
    ("Integrated Chemicals & Materials", "NG=F", "Cost Input", "Inverse (Cost)", "European/global natural gas and naphtha cracker spreads dictate base chemical profitability."),
    ("Integrated Chemicals & Materials", "CL=F", "Cost Input", "Inverse (Cost)", "Crude-to-naphtha feedstock costs impact downstream polyolefin and polyurethane margins."),
    ("Integrated Chemicals & Materials", "BZ=F", "Cost Input", "Inverse (Cost)", "Brent crude sets European and Asian naphtha cracker feedstock costs."),
    ("Integrated Chemicals & Materials", "PNGASEUUSDM", "Cost Input", "Inverse (Cost)", "European TTF natural gas dictates EU chemical plant competitiveness."),
    ("Integrated Chemicals & Materials", "WPU061", "Revenue Proxy", "Direct (Demand)", "Industrial chemical PPI tracks base chemical pricing power."),
    ("Olefins & Polyolefins", "CL=F", "Margin Spread", "Direct (Demand)", "US ethane-to-crude oil cost advantage widens North American polyethylene margins when oil rises."),
    ("Packaging & Specialty Plastics", "NG=F", "Cost Input", "Inverse (Cost)", "USGC ethane/natural gas feedstock costs vs. global polyethylene clearing prices."),
    ("Packaging & Specialty Plastics", "WPU066", "Revenue Proxy", "Direct (Demand)", "Plastic resin PPI tracks polymer conversion spreads."),
    ("Fluoropolymers & Titanium Tech", "SMH", "Revenue Proxy", "Direct (Demand)", "High-purity PFA fluoropolymers essential for semiconductor fab piping and chemical handling."),
    ("Medical Devices & Biomaterials", "IHI", "Revenue Proxy", "Direct (Demand)", "Procedure volumes and med-tech capex drive biocompatible polymer & tissue demand."),
]


# Pydantic Schemas
class CommodityDriverLink(BaseModel):
    commodity_name: str = Field(
        description="Clean, human-readable name of the specific commodity, feedstock, energy input, or end-market driver (e.g., 'High-Purity Nickel', 'Natural Gas', 'Lithium Carbonate', 'Titanium Sponge', 'Copper Foil', 'Epoxy Resins'). Never return a ticker symbol or FRED code here."
    )
    matched_input_code: Optional[str] = Field(
        default=None,
        description="Closest matching code from the canonical input list if applicable (e.g. 'PNICKUSDM', 'NG=F', 'HG=F', 'LICARB_CN', 'SMH', 'ITA'), otherwise null.",
    )
    exposure_role: str = Field(
        description="Must be one of: 'Cost Input', 'Revenue Driver', 'Margin Spread', or 'Byproduct Credit'."
    )
    impact_direction: str = Field(
        description="Must be one of: 'Pass-Through', 'Inverse (Cost)', 'Direct (Demand)', or 'Hedged / Fixed'."
    )
    pass_through_lag: str = Field(
        description="Pricing adjustment speed, e.g. 'Monthly Index', '1-2 Quarter Lag', 'Multi-Year LTA', 'Spot / Unhedged', or 'Take-or-Pay'."
    )
    cogs_or_rev_share_pct: Optional[float] = Field(
        default=None,
        description="Disclosed % of COGS or revenue if explicitly stated, otherwise null.",
    )
    mechanism_detail: str = Field(
        description="2-sentence institutional explanation of how this specific commodity impacts this company's gross margins, surcharges, inventory valuation, or revenue."
    )
    source_quote: str = Field(
        description="Short verbatim phrase from the text referencing this raw material, energy input, or commodity driver."
    )


class DriverBackfillBatch(BaseModel):
    commodity_drivers: List[CommodityDriverLink] = Field(
        description="3 to 6 specific, company-level commodity, raw material, and energy drivers."
    )


class SupplyChainLink(BaseModel):
    counterparty_name: str = Field(
        description="Exact company name if explicitly disclosed (e.g., 'Apple', 'TSMC', 'Boeing'), OR named end-market/material group if confidential."
    )
    is_named_entity: bool = Field(
        description="True ONLY if a specific company/organization name is explicitly stated in the text."
    )
    material_or_product: str = Field(
        description="Specific raw material purchased, overlapping product/segment (for competitors), or engineered product sold."
    )
    is_sole_source: bool = Field(
        description="True if text notes single-source, sole-supplier, primary/direct rival, or 10%+ customer concentration."
    )
    exposure_pct: Optional[float] = Field(
        default=None,
        description="Disclosed % of revenue, raw material spend, or market share if stated, otherwise null.",
    )
    source_quote: str = Field(
        description="Verbatim 10-30 word quote from the source text proving this supplier, competitor, or customer link."
    )


class FilingSummarySchema(BaseModel):
    fiscal_period: str = Field(
        description="Exact reporting period covered (e.g., 'FY2025 10-K', 'Q3 2026 10-Q', or 'Business Profile & Recent News')."
    )
    headline: str = Field(description="Concise 1-sentence institutional headline summarizing the filing/earnings.")
    executive_summary: str = Field(description="3-4 sentence analytical summary of revenue drivers, volume trends, and strategic positioning.")
    theme_tags: List[str] = Field(description="2 to 5 tags from: ['AI Infrastructure', 'Aerospace & Defense', 'Electrification & Grid', 'EV & Batteries', 'Semiconductors', 'Medical Devices', 'Industrial Recovery', 'Commodity Headwind', 'Pricing Power']")
    pricing_and_margins: Optional[str] = Field(
        default=None,
        description="Specific details on gross/operating margins, raw material pass-through, surcharges, or price/cost spread ONLY if stated in text; otherwise null.",
    )
    capex_and_backlog: Optional[str] = Field(
        default=None,
        description="Details on order backlog, customer qualification milestones, capacity expansion, or capex guidance ONLY if explicitly stated in text; NEVER estimate or use outside memory. Return null if not disclosed.",
    )
    key_risks: str = Field(description="Top 1-2 operational, geopolitical, or cyclical risks highlighted in the report.")
    commodity_drivers: List[CommodityDriverLink] = Field(
        default_factory=list,
        description="3 to 6 specific raw materials, energy inputs, feedstocks, and commodity benchmarks that drive this specific company's costs or revenues.",
    )
    upstream_suppliers: List[SupplyChainLink] = Field(
        default_factory=list,
        description="Explicitly disclosed upstream raw material suppliers, feedstock sources, or sole-source inputs found in the text.",
    )
    competitors: List[SupplyChainLink] = Field(
        default_factory=list,
        description="Explicitly named competitors or rival manufacturers disclosed in Item 1 Competition or MD&A.",
    )
    downstream_customers: List[SupplyChainLink] = Field(
        default_factory=list,
        description="Explicitly disclosed downstream customers, OEMs, or major customer concentration disclosures found in the text.",
    )


class MacroNarrativeSchema(BaseModel):
    overall_regime: str = Field(description="Short 4-7 word regime label, e.g. 'AI & Aerospace Boom vs. Chemical Destocking'")
    executive_brief: str = Field(description="4-5 sentence institutional macro synthesis across the advanced materials value chain.")
    ai_theme_summary: str = Field(description="3-4 sentences synthesizing what filings say about AI data centers, HBM, substrates, optical fiber, and power.")
    aerospace_theme_summary: str = Field(description="3-4 sentences synthesizing commercial aero build rates, titanium/alloy bottlenecks, composites, and defense.")
    commodities_theme_summary: str = Field(description="3-4 sentences connecting current copper, natural gas, oil, lithium, and rare earth prices to company margins.")
    electrification_battery_summary: str = Field(description="3-4 sentences synthesizing EV battery destocking, grid transformers, nuclear/uranium, and rare earth magnets.")


class ScopeInsight(BaseModel):
    scope_type: str = Field(description="Must be 'industry', 'sub_industry', or 'theme'.")
    scope_name: str = Field(description="Exact name of the industry ('Advanced Materials'), sub-industry, or theme.")
    sentiment: str = Field(description="One of: 'Bullish', 'Mixed', 'Cautious', 'Bearish'.")
    headline: str = Field(description="1-sentence institutional takeaway backed by the filings.")
    key_drivers: str = Field(description="2-3 sentences on demand, volume, and end-market momentum.")
    pricing_and_margins: str = Field(description="2 sentences on pricing power, surcharges, and raw material pass-through.")
    capex_and_supply_chain: str = Field(description="2 sentences on backlog, qualification bottlenecks, or capacity expansion.")
    top_tickers: List[str] = Field(description="Up to 5 tickers from the provided filings that best illustrate this insight.")


class InsightBatchSchema(BaseModel):
    insights: List[ScopeInsight]


def _quote_exists(quote: str, source_text: str) -> bool:
    if not quote or len(quote.strip()) < 10:
        return False
    clean_q = re.sub(r"[^a-z0-9 ]+", " ", quote.lower()).split()
    clean_s = " ".join(re.sub(r"[^a-z0-9 ]+", " ", source_text.lower()).split())
    if len(clean_q) < 4:
        return " ".join(clean_q) in clean_s
    return any(" ".join(clean_q[i : i + 4]) in clean_s for i in range(len(clean_q) - 3))


def save_commodity_drivers(conn, ticker: str, filing_type: str, filing_date: str, drivers: list):
    conn = ensure_conn(conn)
    rows = []
    for d in (drivers or []):
        cname = (d.get("commodity_name") or "").strip()
        mech = (d.get("mechanism_detail") or "").strip()
        if not cname or not mech:
            continue
        code = (d.get("matched_input_code") or "").strip() or None
        rows.append((
            ticker,
            cname[:100],
            code[:32] if code else None,
            (d.get("exposure_role") or "Cost Input")[:50],
            (d.get("impact_direction") or "Pass-Through")[:50],
            (d.get("pass_through_lag") or "Quarterly")[:60],
            _safe_pct(d.get("cogs_or_rev_share_pct")),
            mech[:600],
            filing_type,
            filing_date,
            (d.get("source_quote") or "")[:350],
        ))

    if not rows:
        return conn

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
            rows,
        )
    conn.commit()
    print(f"Saved {len(rows)} AI commodity drivers for {ticker}")
    return conn


def save_supply_chain_links(conn, ticker: str, filing_type: str, filing_date: str, parsed: dict, source_text: str):
    conn = ensure_conn(conn)
    rows = []
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
            rows.append((
                ticker,
                direction,
                cname[:140],
                bool(item.get("is_named_entity", False)),
                mat[:180],
                bool(item.get("is_sole_source", False)),
                _safe_pct(item.get("exposure_pct")),
                filing_type,
                filing_date,
                quote[:400],
            ))

    if not rows:
        return conn

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
            rows,
        )
    conn.commit()
    return conn


def seed_sensitivity_map(conn):
    conn = ensure_conn(conn)
    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO commodity_sensitivity (sub_sector, input_code, relationship_type, impact_direction, mechanism_note)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (sub_sector, input_code) DO UPDATE SET
                relationship_type = EXCLUDED.relationship_type,
                impact_direction = EXCLUDED.impact_direction,
                mechanism_note = EXCLUDED.mechanism_note;
        """, SENSITIVITY_RULES)
    print(f"Upserted {len(SENSITIVITY_RULES)} commodity sensitivity rules.")
    return conn


def ensure_ai_tables(conn):
    conn = ensure_conn(conn)
    with conn.cursor() as cur:
        cur.execute("""
            ALTER TABLE company_filings_ai
              ADD COLUMN IF NOT EXISTS fiscal_period TEXT;

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

            CREATE INDEX IF NOT EXISTS idx_supply_chain_ticker ON equity_supply_chain(ticker);
            CREATE INDEX IF NOT EXISTS idx_commodity_drivers_ticker ON equity_commodity_drivers(ticker);
        """)
    conn.commit()
    return conn


def fetch_sec_ticker_map():
    resp = requests.get("https://www.sec.gov/files/company_tickers.json", headers=SEC_HEADERS, timeout=20)
    resp.raise_for_status()
    raw = resp.json()
    mapping = {}
    for entry in raw.values():
        t = entry["ticker"].upper()
        cik_padded = str(entry["cik_str"]).zfill(10)
        mapping[t] = (cik_padded, str(entry["cik_str"]))
    return mapping


def _extract_keyword_windows(text: str, patterns: list[str], window: int = 1800, max_total: int = 8000) -> str:
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


def extract_filing_text(cik_unpadded: str, accession_number: str, primary_doc: str) -> str:
    acc_no_dash = accession_number.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{cik_unpadded}/{acc_no_dash}/{primary_doc}"
    resp = requests.get(url, headers=SEC_HEADERS, timeout=25)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text)

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

    item1_matches = list(re.finditer(r"Item\s+1\.\s+Business", text, re.IGNORECASE))
    mda_matches = list(re.finditer(r"(Item\s+2\.|Item\s+7\.)\s+Management.{0,30}Discussion", text, re.IGNORECASE))

    item1_slice = ""
    if item1_matches:
        start1 = item1_matches[-1].start()
        item1_slice = text[start1 : start1 + 6500]

    mda_slice = ""
    if mda_matches:
        start_mda = mda_matches[-1].start()
        mda_slice = text[start_mda : start_mda + 12500]
    else:
        mda_slice = text[5000:17500]

    combined = f"{sc_windows} ... {item1_slice} ... {mda_slice}".strip(" .")
    return combined[:28000]


def process_sec_filings(conn, limit_tickers=5):
    conn = ensure_conn(conn)
    sec_map = fetch_sec_ticker_map()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT e.ticker, e.company_name, e.sub_sector
            FROM equities_universe e
            LEFT JOIN company_filings_ai f ON e.ticker = f.ticker
            GROUP BY e.ticker, e.company_name, e.sub_sector, e.qualification_years
            ORDER BY COUNT(f.id) ASC, e.qualification_years DESC NULLS LAST, e.ticker ASC;
        """)
        all_equities = cur.fetchall()

    processed = 0
    for ticker, company_name, sub_sector in all_equities:
        if processed >= limit_tickers:
            break
        clean_ticker = ticker.upper()
        if clean_ticker not in sec_map:
            continue

        cik_padded, cik_unpadded = sec_map[clean_ticker]
        try:
            conn = ensure_conn(conn)
            sub_url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
            sub_resp = requests.get(sub_url, headers=SEC_HEADERS, timeout=15)
            sub_resp.raise_for_status()
            recent = sub_resp.json()["filings"]["recent"]

            filings_found = 0
            new_added = 0
            all_apis_exhausted = False
            for i, form in enumerate(recent["form"]):
                if form in ("10-K", "10-Q") and filings_found < 3:
                    f_date = recent["filingDate"][i]
                    acc_num = recent["accessionNumber"][i]
                    doc_name = recent["primaryDocument"][i]
                    source_url = f"https://www.sec.gov/Archives/edgar/data/{cik_unpadded}/{acc_num.replace('-', '')}/{doc_name}"

                    conn = ensure_conn(conn)
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT 1 FROM company_filings_ai WHERE ticker = %s AND filing_type = %s AND filing_date = %s;",
                            (ticker, form, f_date),
                        )
                        if cur.fetchone():
                            filings_found += 1
                            continue

                    excerpt = extract_filing_text(cik_unpadded, acc_num, doc_name)
                    if len(excerpt) < 500:
                        continue

                    prompt = f"""
                    You are an institutional advanced materials equity analyst. Analyze this SEC {form} filing excerpt for {company_name} ({ticker}), sub-sector: {sub_sector}, filed on {f_date}.
                    1. Extract operational insights on demand trends, pricing power/raw material pass-through, backlog/capex (ONLY if stated in the excerpt; return null if not disclosed), and risks.
                    2. Extract 3 to 6 specific company-level commodity, raw material, feedstock, and energy drivers (using human-readable names like 'High-Purity Nickel' or 'Natural Gas', never ticker symbols) and match them to the closest code in this list when applicable:
                    {CANONICAL_INPUT_GUIDE}
                    3. Extract any explicitly disclosed upstream raw material suppliers, named competitors, or downstream customers with verbatim quotes.

                    FILING EXCERPT:
                    {excerpt}
                    """

                    ai_resp = call_gemini(prompt, FilingSummarySchema)
                    if not ai_resp:
                        all_apis_exhausted = True
                        break
                    parsed = json.loads(ai_resp.text)

                    conn = ensure_conn(conn)
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO company_filings_ai (
                                ticker, filing_type, filing_date, fiscal_period, headline, executive_summary,
                                theme_tags, pricing_and_margins, capex_and_backlog, key_risks, source_url
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (ticker, filing_type, filing_date) DO UPDATE SET
                                fiscal_period = EXCLUDED.fiscal_period,
                                headline = EXCLUDED.headline,
                                executive_summary = EXCLUDED.executive_summary,
                                theme_tags = EXCLUDED.theme_tags,
                                pricing_and_margins = EXCLUDED.pricing_and_margins,
                                capex_and_backlog = EXCLUDED.capex_and_backlog,
                                key_risks = EXCLUDED.key_risks,
                                source_url = EXCLUDED.source_url;
                        """, (
                            ticker,
                            form,
                            f_date,
                            parsed.get("fiscal_period") or f"{form} ({f_date})",
                            parsed["headline"],
                            parsed["executive_summary"],
                            parsed["theme_tags"],
                            parsed.get("pricing_and_margins"),
                            parsed.get("capex_and_backlog"),
                            parsed["key_risks"],
                            source_url,
                        ))
                    conn.commit()
                    conn = save_commodity_drivers(conn, ticker, form, f_date, parsed.get("commodity_drivers", []))
                    conn = save_supply_chain_links(conn, ticker, form, f_date, parsed, excerpt)
                    filings_found += 1
                    new_added += 1
                    print(f"Summarized {ticker} {form} ({f_date})")
                    time.sleep(0.25)

            if all_apis_exhausted:
                break
            if new_added > 0:
                processed += 1
        except Exception as e:
            print(f"Warning processing SEC filings for {ticker}: {e}")
            conn = ensure_conn(conn)

    return conn


def process_international_briefs(conn, limit_tickers=4):
    conn = ensure_conn(conn)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT e.ticker, e.company_name, e.sub_sector, e.contract_type, e.qualification_years
            FROM equities_universe e
            LEFT JOIN company_filings_ai f ON e.ticker = f.ticker
            WHERE f.ticker IS NULL
            ORDER BY e.qualification_years DESC NULLS LAST
            LIMIT %s;
        """, (limit_tickers,))
        missing = cur.fetchall()

    today_str = date.today().isoformat()
    for ticker, company_name, sub_sector, contract_type, qual_years in missing:
        try:
            tk = yf.Ticker(ticker)
            info = tk.info or {}
            news_items = tk.news or []
            headlines = [n.get("content", {}).get("title", "") or n.get("title", "") for n in news_items[:6]]
            headlines_str = "\n".join([f"- {h}" for h in headlines if h])
            biz_summary = info.get("longBusinessSummary", "")[:2500]
            rev_growth = info.get("revenueGrowth", "N/A")
            gross_margin = info.get("grossMargins", "N/A")
            ebitda_margin = info.get("ebitdaMargins", "N/A")
            source_text = f"{biz_summary}\n{headlines_str}"

            prompt = f"""
            You are an institutional advanced materials equity analyst covering global equities.
            Synthesize a structured research & earnings brief for {company_name} ({ticker}), Sub-sector: {sub_sector}, Contract Type: {contract_type}, Qualification Moat: {qual_years} yrs.
            Financial Metrics: Revenue Growth={rev_growth}, Gross Margin={gross_margin}, EBITDA Margin={ebitda_margin}.
            CRITICAL: Only report capex_and_backlog or pricing_and_margins if explicitly stated in the Business Profile or News Headlines below. NEVER guess or invent capex figures from outside memory—return null if not stated.
            Extract 3 to 6 specific company-level commodity, raw material, and energy drivers (using human-readable names, never ticker codes) matched to this canonical list when applicable:
            {CANONICAL_INPUT_GUIDE}
            Also extract any explicitly mentioned upstream suppliers, named competitors, or downstream customers with verbatim quotes.
            Business Profile: {biz_summary}
            Recent News Headlines:
            {headlines_str}
            """

            ai_resp = call_gemini(prompt, FilingSummarySchema)
            if not ai_resp:
                break
            parsed = json.loads(ai_resp.text)

            conn = ensure_conn(conn)
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO company_filings_ai (
                        ticker, filing_type, filing_date, fiscal_period, headline, executive_summary,
                        theme_tags, pricing_and_margins, capex_and_backlog, key_risks, source_url
                    ) VALUES (%s, 'Earnings Brief', CURRENT_DATE, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ticker, filing_type, filing_date) DO UPDATE SET
                        fiscal_period = EXCLUDED.fiscal_period,
                        headline = EXCLUDED.headline,
                        executive_summary = EXCLUDED.executive_summary,
                        theme_tags = EXCLUDED.theme_tags,
                        pricing_and_margins = EXCLUDED.pricing_and_margins,
                        capex_and_backlog = EXCLUDED.capex_and_backlog,
                        key_risks = EXCLUDED.key_risks;
                """, (
                    ticker,
                    parsed.get("fiscal_period") or "Company Profile & News",
                    parsed["headline"],
                    parsed["executive_summary"],
                    parsed["theme_tags"],
                    parsed.get("pricing_and_margins"),
                    parsed.get("capex_and_backlog"),
                    parsed["key_risks"],
                    f"https://finance.yahoo.com/quote/{ticker}",
                ))
            conn.commit()
            conn = save_commodity_drivers(conn, ticker, "Earnings Brief", today_str, parsed.get("commodity_drivers", []))
            conn = save_supply_chain_links(conn, ticker, "Earnings Brief", today_str, parsed, source_text)
            print(f"Generated global brief for {ticker}")
            time.sleep(0.3)
        except Exception as e:
            print(f"Warning generating international brief for {ticker}: {e}")
            conn = ensure_conn(conn)

    return conn


def backfill_existing_filing_drivers(conn, limit_tickers=4):
    """Populates per-stock AI commodity drivers for stocks whose filings were already parsed in earlier runs."""
    conn = ensure_conn(conn)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (f.ticker)
                f.ticker, e.company_name, e.sub_sector, e.contract_type,
                f.filing_type, f.filing_date::text, f.executive_summary,
                f.pricing_and_margins, f.capex_and_backlog, f.key_risks
            FROM company_filings_ai f
            JOIN equities_universe e ON f.ticker = e.ticker
            LEFT JOIN equity_commodity_drivers d ON f.ticker = d.ticker
            WHERE d.ticker IS NULL
            ORDER BY f.ticker, f.filing_date DESC
            LIMIT %s;
        """, (limit_tickers,))
        rows = cur.fetchall()

    for ticker, company_name, sub_sector, contract_type, ftype, fdate, summary, margins, capex, risks in rows:
        prompt = f"""
        You are an institutional advanced materials commodity & equity analyst.
        For {company_name} ({ticker}), Sub-sector: {sub_sector}, Contract Type: {contract_type}, identify 4 to 6 specific raw materials, energy inputs, feedstocks, and commodity benchmarks that drive its costs, margins, or revenues.
        Use clean human-readable commodity names (never ticker symbols or FRED codes) and map each to the closest canonical code when applicable:
        {CANONICAL_INPUT_GUIDE}

        FILING CONTEXT ({ftype} {fdate}):
        Summary: {summary}
        Pricing & Margins: {margins}
        Capex & Backlog: {capex}
        Risks: {risks}
        """
        ai_resp = call_gemini(prompt, DriverBackfillBatch)
        if not ai_resp:
            break
        parsed = json.loads(ai_resp.text)
        conn = save_commodity_drivers(conn, ticker, ftype, fdate, parsed.get("commodity_drivers", []))

    return conn


class SupplyChainBackfillBatch(BaseModel):
    upstream_suppliers: List[SupplyChainLink] = Field(default_factory=list)
    competitors: List[SupplyChainLink] = Field(default_factory=list)
    downstream_customers: List[SupplyChainLink] = Field(default_factory=list)


def backfill_existing_supply_chain(conn, limit_tickers=4):
    """Extracts upstream suppliers, competitors & downstream customers for US stocks whose filings were already parsed."""
    conn = ensure_conn(conn)
    sec_map = fetch_sec_ticker_map()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT e.ticker, e.company_name, e.sub_sector, e.qualification_years
            FROM equities_universe e
            JOIN company_filings_ai f ON e.ticker = f.ticker
            LEFT JOIN equity_supply_chain sc ON e.ticker = sc.ticker
            WHERE sc.ticker IS NULL
            ORDER BY e.qualification_years DESC NULLS LAST, e.ticker ASC;
        """)
        candidates = cur.fetchall()

    processed = 0
    for ticker, company_name, sub_sector, _ in candidates:
        if processed >= limit_tickers:
            break
        clean_ticker = ticker.upper()
        if clean_ticker not in sec_map:
            continue

        cik_padded, cik_unpadded = sec_map[clean_ticker]
        try:
            sub_url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
            sub_resp = requests.get(sub_url, headers=SEC_HEADERS, timeout=15)
            sub_resp.raise_for_status()
            recent = sub_resp.json()["filings"]["recent"]

            target_idx = next((i for i, f in enumerate(recent["form"]) if f == "10-K"), None)
            if target_idx is None:
                target_idx = next((i for i, f in enumerate(recent["form"]) if f == "10-Q"), None)
            if target_idx is None:
                continue

            form = recent["form"][target_idx]
            f_date = recent["filingDate"][target_idx]
            acc_num = recent["accessionNumber"][target_idx]
            doc_name = recent["primaryDocument"][target_idx]

            excerpt = extract_filing_text(cik_unpadded, acc_num, doc_name)
            if len(excerpt) < 500:
                continue

            prompt = f"""
            You are an institutional advanced materials supply-chain analyst.
            Analyze this SEC {form} filing excerpt for {company_name} ({ticker}), sub-sector: {sub_sector}.
            Extract ALL disclosed:
            1. upstream_suppliers: Upstream raw material suppliers, feedstock sources, or sole-source/single-source inputs.
            2. competitors: Explicitly named rival companies disclosed in Item 1 Competition or MD&A.
            3. downstream_customers: Downstream customers, OEMs, or 10%+ customer concentration disclosures.
            Include named companies when disclosed (e.g. Boeing, TSMC, Apple, Airbus) OR named counterparty groups when anonymized (e.g. 'Customer A', 'Tier-1 Aerospace OEMs'), along with verbatim quotes from the text.

            FILING EXCERPT:
            {excerpt}
            """

            ai_resp = call_gemini(prompt, SupplyChainBackfillBatch)
            if not ai_resp:
                break
            parsed = json.loads(ai_resp.text)
            conn = save_supply_chain_links(conn, ticker, form, f_date, parsed, excerpt)
            processed += 1
            print(f"Backfilled supply chain & competitors for {ticker} ({form} {f_date})")
            time.sleep(0.25)
        except Exception as e:
            print(f"Warning backfilling supply chain for {ticker}: {e}")
            conn = ensure_conn(conn)

    return conn


def generate_market_macro_narrative(conn):
    conn = ensure_conn(conn)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (input_code) input_code, input_name, value, unit, observation_date
            FROM raw_inputs
            ORDER BY input_code, observation_date DESC;
        """)
        commodities = cur.fetchall()

        cur.execute("""
            SELECT f.ticker, e.company_name, e.sub_sector, f.filing_type, f.filing_date,
                   f.headline, f.executive_summary, f.pricing_and_margins, f.capex_and_backlog
            FROM company_filings_ai f
            JOIN equities_universe e ON f.ticker = e.ticker
            ORDER BY f.filing_date DESC
            LIMIT 60;
        """)
        filings = cur.fetchall()

    if not filings:
        print("No filings available yet to synthesize macro narrative.")
        return conn

    comm_text = "\n".join([f"- {c[1]}: {c[2]} {c[3]} (as of {c[4]})" for c in commodities])
    filings_text = "\n".join([
        f"[{f[0]} - {f[1]} | {f[2]} | {f[3]} {f[4]}]: {f[5]} Summary: {f[6]} Margins: {f[7] or 'N/A'} Backlog/Capex: {f[8] or 'N/A'}"
        for f in filings
    ])

    prompt = f"""
    You are the Chief Materials & Industrial Strategist at an institutional macro fund.
    Synthesize the live commodity/macro backdrop alongside the latest 10-K, 10-Q, and earnings reports across our global advanced materials universe into a cohesive, actionable market narrative.
    Connect specific company commentary directly to commodity price trends, AI infrastructure capex, aerospace/defense build rates, and battery/electrification supply chains.

    LIVE COMMODITIES & MACRO INPUTS:
    {comm_text}

    RECENT COMPANY 10-K / 10-Q / EARNINGS SUMMARIES:
    {filings_text}
    """

    ai_resp = call_gemini(prompt, MacroNarrativeSchema)
    if not ai_resp:
        return conn
    parsed = json.loads(ai_resp.text)

    conn = ensure_conn(conn)
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO macro_narrative (
                generated_date, overall_regime, executive_brief,
                ai_theme_summary, aerospace_theme_summary,
                commodities_theme_summary, electrification_battery_summary
            ) VALUES (CURRENT_DATE, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (generated_date) DO UPDATE SET
                overall_regime = EXCLUDED.overall_regime,
                executive_brief = EXCLUDED.executive_brief,
                ai_theme_summary = EXCLUDED.ai_theme_summary,
                aerospace_theme_summary = EXCLUDED.aerospace_theme_summary,
                commodities_theme_summary = EXCLUDED.commodities_theme_summary,
                electrification_battery_summary = EXCLUDED.electrification_battery_summary;
        """, (
            parsed["overall_regime"],
            parsed["executive_brief"],
            parsed["ai_theme_summary"],
            parsed["aerospace_theme_summary"],
            parsed["commodities_theme_summary"],
            parsed["electrification_battery_summary"],
        ))
    conn.commit()
    print("Generated overarching AI Market & Theme Macro Narrative.")
    return conn


def generate_scoped_insights(conn):
    conn = ensure_conn(conn)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (f.ticker)
                f.ticker, e.company_name, e.sub_sector, f.filing_type, f.filing_date,
                f.headline, f.executive_summary, f.pricing_and_margins,
                f.capex_and_backlog, f.theme_tags
            FROM company_filings_ai f
            JOIN equities_universe e ON f.ticker = e.ticker
            ORDER BY f.ticker, f.filing_date DESC;
        """)
        rows = cur.fetchall()

    if not rows:
        print("No filings available yet for scoped insights.")
        return conn

    by_sub = {}
    by_theme = {}
    all_lines = []

    for ticker, cname, sub_sector, ftype, fdate, headline, summary, margins, capex, tags in rows:
        line = (
            f"[{ticker} ({cname}) | {ftype} {fdate}] {headline} | "
            f"Summary: {summary} | Margins: {margins or 'N/A'} | Capex/Backlog: {capex or 'N/A'}"
        )
        all_lines.append(line)
        if sub_sector:
            by_sub.setdefault(sub_sector, []).append((ticker, line))
        for tag in (tags or []):
            by_theme.setdefault(tag, []).append((ticker, line))

    all_insights = []
    sub_items = sorted(by_sub.items(), key=lambda x: -len(x[1]))

    for i in range(0, len(sub_items), 6):
        batch = sub_items[i : i + 6]
        blocks = []
        allowed_by_sub = {}
        for sub_name, entries in batch:
            allowed_by_sub[sub_name] = {tk for tk, _ in entries}
            joined = "\n  ".join(ln for _, ln in entries[:8])
            blocks.append(f"=== SUB-INDUSTRY: {sub_name} (Filing Count: {len(entries)}) ===\n  {joined}")

        prompt = f"""
        You are a Senior Advanced Materials Equity Strategist.
        Generate one ScopeInsight (with scope_type='sub_industry') for EACH sub-industry block below.
        CRITICAL RULES:
        - Use ONLY the filings listed inside that sub-industry's block. NEVER reference tickers from another block.
        - Because every block listed below has at least 1 filing, NEVER say there are no filings or no data.
        - Set top_tickers ONLY to tickers present inside that exact block.

        {chr(10).join(blocks)}
        """
        ai_resp = call_gemini(prompt, InsightBatchSchema)
        if not ai_resp:
            break
        for item in json.loads(ai_resp.text).get("insights", []):
            sname = item.get("scope_name")
            if sname in allowed_by_sub:
                item["scope_type"] = "sub_industry"
                item["top_tickers"] = [t for t in item.get("top_tickers", []) if t in allowed_by_sub[sname]][:5]
                all_insights.append(item)

    theme_items = sorted(by_theme.items(), key=lambda x: -len(x[1]))[:8]
    theme_blocks = []
    allowed_by_theme = {"Advanced Materials": {r[0] for r in rows}}
    for tname, entries in theme_items:
        allowed_by_theme[tname] = {tk for tk, _ in entries}
        joined = "\n  ".join(ln for _, ln in entries[:8])
        theme_blocks.append(f"=== THEME: {tname} (Filing Count: {len(entries)}) ===\n  {joined}")

    theme_prompt = f"""
    You are a Senior Advanced Materials Equity Strategist.
    Generate:
    1. One ScopeInsight with scope_type='industry' and scope_name='Advanced Materials' summarizing the overall universe below.
    2. One ScopeInsight with scope_type='theme' for EACH theme block below, using ONLY the filings inside that theme's block.

    === INDUSTRY: Advanced Materials (Total Covered Companies: {len(rows)}) ===
    {chr(10).join(all_lines[:25])}

    {chr(10).join(theme_blocks)}
    """
    ai_resp = call_gemini(theme_prompt, InsightBatchSchema)
    if ai_resp:
        for item in json.loads(ai_resp.text).get("insights", []):
            sname = item.get("scope_name")
            if sname in allowed_by_theme:
                item["top_tickers"] = [t for t in item.get("top_tickers", []) if t in allowed_by_theme[sname]][:5]
                all_insights.append(item)

    conn = ensure_conn(conn)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM sector_theme_insights;")
        for item in all_insights:
            stype = item["scope_type"]
            sname = item["scope_name"]
            count = (
                len(rows)
                if stype == "industry"
                else len(by_sub.get(sname, []))
                if stype == "sub_industry"
                else len(by_theme.get(sname, []))
            )
            cur.execute("""
                INSERT INTO sector_theme_insights (
                    scope_type, scope_name, generated_date, filing_count, sentiment,
                    headline, key_drivers, pricing_and_margins, capex_and_supply_chain, top_tickers
                ) VALUES (%s, %s, CURRENT_DATE, %s, %s, %s, %s, %s, %s, %s);
            """, (
                stype, sname, count, item["sentiment"], item["headline"],
                item["key_drivers"], item["pricing_and_margins"],
                item["capex_and_supply_chain"], item["top_tickers"],
            ))
    conn.commit()
    print(f"Upserted {len(all_insights)} validated industry, sub-industry, and theme insights.")
    return conn


if __name__ == "__main__":
    conn = connect_db()
    try:
        conn = ensure_ai_tables(conn)
        conn = seed_sensitivity_map(conn)
        conn = process_sec_filings(conn, limit_tickers=5)
        conn = process_international_briefs(conn, limit_tickers=4)
        conn = backfill_existing_filing_drivers(conn, limit_tickers=4)
        conn = backfill_existing_supply_chain(conn, limit_tickers=4)
        try:
            conn = generate_market_macro_narrative(conn)
            conn = generate_scoped_insights(conn)
        except Exception as e:
            print(f"Skipping synthesis due to API error: {e}")
    finally:
        if conn and not conn.closed:
            conn.close()
    print(f"AI Filings & Macro Synthesis pipeline finished ({requests_used} Gemini calls, {groq_requests_used} Groq calls used).")
