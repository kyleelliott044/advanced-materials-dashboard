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

MAX_REQUESTS_PER_RUN = 19
requests_used = 0
groq_requests_used = 0
quota_exhausted = False

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


class _FallbackResp:
    def __init__(self, text: str):
        self.text = text


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


def call_gemini(prompt: str, schema, retries: int = 3):
    global requests_used, quota_exhausted

    if not quota_exhausted and requests_used < MAX_REQUESTS_PER_RUN:
        for attempt in range(retries):
            try:
                requests_used += 1
                return client.models.generate_content(
                    model="gemini-3.8-flash",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=schema,
                    ),
                )
            except Exception as e:
                err = str(e)
                if "429" in err or "RESOURCE_EXHAUSTED" in err:
                    quota_exhausted = True
                    print("Daily Gemini quota reached — switching to Groq fallback.")
                    break
                if any(k in err for k in ("503", "UNAVAILABLE", "disconnected", "timed out")):
                    if attempt < retries - 1:
                        wait = 3 * (2 ** attempt)
                        print(f"Gemini 503/transient error — retrying in {wait}s...")
                        time.sleep(wait)
                        continue
                    print("Gemini still unavailable — switching to Groq fallback.")
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
        description="Closest matching code from the canonical input list if applicable (e.g. 'PNICKUSDM', 'NG=F', 'HG=F', 'LICARB_CN', 'SMH', 'ITA'), otherwise null."
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
        description="Disclosed % of COGS or revenue if explicitly stated, otherwise null."
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
        description="Specific raw material purchased or engineered product sold."
    )
    is_sole_source: bool = Field(
        description="True if text notes single-source, sole-supplier, or 10%+ customer concentration."
    )
    exposure_pct: Optional[float] = Field(
        default=None,
        description="Disclosed % of revenue or raw material spend if stated, otherwise null.",
    )
    source_quote: str = Field(
        description="Verbatim 10-30 word quote from the source text proving this supplier or customer link."
    )


class FilingSummarySchema(BaseModel):
    headline: str = Field(description="Concise 1-sentence institutional headline summarizing the filing/earnings.")
    executive_summary: str = Field(description="3-4 sentence analytical summary of revenue drivers, volume trends, and strategic positioning.")
    theme_tags: List[str] = Field(description="2 to 5 tags from: ['AI Infrastructure', 'Aerospace & Defense', 'Electrification & Grid', 'EV & Batteries', 'Semiconductors', 'Medical Devices', 'Industrial Recovery', 'Commodity Headwind', 'Pricing Power']")
    pricing_and_margins: str = Field(description="Specific details on gross/operating margins, raw material pass-through, surcharges, or price/cost spread.")
    capex_and_backlog: str = Field(description="Details on order backlog, customer qualification milestones, capacity expansion, or capex guidance.")
    key_risks: str = Field(description="Top 1-2 operational, geopolitical, or cyclical risks highlighted in the report.")
    commodity_drivers: List[CommodityDriverLink] = Field(
        default_factory=list,
        description="3 to 6 specific raw materials, energy inputs, feedstocks, and commodity benchmarks that drive this specific company's costs or revenues."
    )
    upstream_suppliers: List[SupplyChainLink] = Field(
        default_factory=list,
        description="Explicitly disclosed upstream raw material suppliers, feedstock sources, or sole-source inputs found in the text.",
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
    if not quote or len(quote.strip()) < 12:
        return False
    clean_q = re.sub(r"[^a-z0-9 ]+", " ", quote.lower()).split()
    clean_s = " ".join(re.sub(r"[^a-z0-9 ]+", " ", source_text.lower()).split())
    if len(clean_q) < 5:
        return " ".join(clean_q) in clean_s
    return any(" ".join(clean_q[i : i + 6]) in clean_s for i in range(len(clean_q) - 5))


def save_commodity_drivers(conn, ticker: str, filing_type: str, filing_date: str, drivers: list):
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
            d.get("cogs_or_rev_share_pct"),
            mech[:600],
            filing_type,
            filing_date,
            (d.get("source_quote") or "")[:350],
        ))

    if not rows:
        return

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


def save_supply_chain_links(conn, ticker: str, filing_type: str, filing_date: str, parsed: dict, source_text: str):
    rows = []
    for direction, key in (("upstream_supplier", "upstream_suppliers"), ("downstream_customer", "downstream_customers")):
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
                item.get("exposure_pct"),
                filing_type,
                filing_date,
                quote[:400],
            ))

    if not rows:
        return

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


def seed_sensitivity_map(conn):
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


def extract_filing_text(cik_unpadded: str, accession_number: str, primary_doc: str) -> str:
    acc_no_dash = accession_number.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{cik_unpadded}/{acc_no_dash}/{primary_doc}"
    resp = requests.get(url, headers=SEC_HEADERS, timeout=25)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text)

    item1_match = re.search(r"Item\s+1\.\s+Business", text, re.IGNORECASE)
    mda_match = re.search(r"(Item\s+2\.|Item\s+7\.)\s+Management.{0,30}Discussion", text, re.IGNORECASE)

    if item1_match and mda_match and mda_match.start() > item1_match.start():
        item1_slice = text[item1_match.start() : item1_match.start() + 9000]
        mda_slice = text[mda_match.start() : mda_match.start() + 19000]
        return f"{item1_slice} ... {mda_slice}"
    if mda_match:
        start = mda_match.start()
        return text[start : start + 28000]
    return text[5000:33000]


def process_sec_filings(conn, limit_tickers=8):
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
                    1. Extract operational insights on demand trends, pricing power/raw material pass-through, backlog/capex, and risks.
                    2. Extract 3 to 6 specific company-level commodity, raw material, feedstock, and energy drivers (using human-readable names like 'High-Purity Nickel' or 'Natural Gas', never ticker symbols) and match them to the closest code in this list when applicable:
                    {CANONICAL_INPUT_GUIDE}
                    3. Extract any explicitly disclosed upstream raw material suppliers or downstream customers with verbatim quotes.

                    FILING EXCERPT:
                    {excerpt}
                    """

                    ai_resp = call_gemini(prompt, FilingSummarySchema)
                    if not ai_resp:
                        all_apis_exhausted = True
                        break
                    parsed = json.loads(ai_resp.text)

                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO company_filings_ai (
                                ticker, filing_type, filing_date, headline, executive_summary,
                                theme_tags, pricing_and_margins, capex_and_backlog, key_risks, source_url
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (ticker, filing_type, filing_date) DO UPDATE SET
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
                            parsed["headline"],
                            parsed["executive_summary"],
                            parsed["theme_tags"],
                            parsed["pricing_and_margins"],
                            parsed["capex_and_backlog"],
                            parsed["key_risks"],
                            source_url,
                        ))
                    conn.commit()
                    save_commodity_drivers(conn, ticker, form, f_date, parsed.get("commodity_drivers", []))
                    save_supply_chain_links(conn, ticker, form, f_date, parsed, excerpt)
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


def process_international_briefs(conn, limit_tickers=5):
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
            Extract 3 to 6 specific company-level commodity, raw material, and energy drivers (using human-readable names, never ticker codes) matched to this canonical list when applicable:
            {CANONICAL_INPUT_GUIDE}
            Also extract any explicitly mentioned upstream suppliers or downstream customers with verbatim quotes.
            Business Profile: {biz_summary}
            Recent News Headlines:
            {headlines_str}
            """

            ai_resp = call_gemini(prompt, FilingSummarySchema)
            if not ai_resp:
                break
            parsed = json.loads(ai_resp.text)

            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO company_filings_ai (
                        ticker, filing_type, filing_date, headline, executive_summary,
                        theme_tags, pricing_and_margins, capex_and_backlog, key_risks, source_url
                    ) VALUES (%s, 'Earnings Brief', CURRENT_DATE, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ticker, filing_type, filing_date) DO UPDATE SET
                        headline = EXCLUDED.headline,
                        executive_summary = EXCLUDED.executive_summary,
                        theme_tags = EXCLUDED.theme_tags,
                        pricing_and_margins = EXCLUDED.pricing_and_margins,
                        capex_and_backlog = EXCLUDED.capex_and_backlog,
                        key_risks = EXCLUDED.key_risks;
                """, (
                    ticker,
                    parsed["headline"],
                    parsed["executive_summary"],
                    parsed["theme_tags"],
                    parsed["pricing_and_margins"],
                    parsed["capex_and_backlog"],
                    parsed["key_risks"],
                    f"https://finance.yahoo.com/quote/{ticker}",
                ))
            conn.commit()
            save_commodity_drivers(conn, ticker, "Earnings Brief", today_str, parsed.get("commodity_drivers", []))
            save_supply_chain_links(conn, ticker, "Earnings Brief", today_str, parsed, source_text)
            print(f"Generated global brief for {ticker}")
            time.sleep(0.3)
        except Exception as e:
            print(f"Warning generating international brief for {ticker}: {e}")


def backfill_existing_filing_drivers(conn, limit_tickers=8):
    """Populates per-stock AI commodity drivers for stocks whose filings were already parsed in earlier runs."""
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
        save_commodity_drivers(conn, ticker, ftype, fdate, parsed.get("commodity_drivers", []))


def generate_market_macro_narrative(conn):
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
        return

    comm_text = "\n".join([f"- {c[1]}: {c[2]} {c[3]} (as of {c[4]})" for c in commodities])
    filings_text = "\n".join([
        f"[{f[0]} - {f[1]} | {f[2]} | {f[3]} {f[4]}]: {f[5]} Summary: {f[6]} Margins: {f[7]} Backlog/Capex: {f[8]}"
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
        return
    parsed = json.loads(ai_resp.text)

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


def generate_scoped_insights(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT f.ticker, e.sub_sector, f.filing_type, f.filing_date,
                   f.headline, f.executive_summary, f.pricing_and_margins,
                   f.capex_and_backlog, f.theme_tags
            FROM company_filings_ai f
            JOIN equities_universe e ON f.ticker = e.ticker
            ORDER BY f.filing_date DESC
            LIMIT 80;
        """)
        rows = cur.fetchall()

    if not rows:
        print("No filings available yet for scoped insights.")
        return

    sub_counts, theme_counts = {}, {}
    filing_lines = []
    for ticker, sub_sector, ftype, fdate, headline, summary, margins, capex, tags in rows:
        if sub_sector:
            sub_counts[sub_sector] = sub_counts.get(sub_sector, 0) + 1
        for tag in (tags or []):
            theme_counts[tag] = theme_counts.get(tag, 0) + 1
        filing_lines.append(
            f"[{ticker} | Sub-Industry: {sub_sector} | Themes: {', '.join(tags or [])} | {ftype} {fdate}] "
            f"{headline} | Margins: {margins} | Capex/Backlog: {capex}"
        )

    active_subs = [k for k, _ in sorted(sub_counts.items(), key=lambda x: -x[1])[:10]]
    active_themes = [k for k, _ in sorted(theme_counts.items(), key=lambda x: -x[1])[:8]]

    prompt = f"""
    You are a Senior Advanced Materials Equity Strategist.
    Using ONLY the parsed SEC filings below, generate structured insight summaries for:
1. Industry (scope_type='industry'): 'Advanced Materials' (synthesizing all filings)
2. Sub-Industries (scope_type='sub_industry'): {', '.join(active_subs)}
3. Themes (scope_type='theme'): {', '.join(active_themes)}

    Be specific: cite actual company tickers, margin/pass-through mechanics, and backlog trends from the filings.

    PARSED FILINGS:
    {chr(10).join(filing_lines)}
    """

    ai_resp = call_gemini(prompt, InsightBatchSchema)
    if not ai_resp:
        return
    parsed = json.loads(ai_resp.text)

    with conn.cursor() as cur:
        for item in parsed["insights"]:
            stype = item["scope_type"]
            sname = item["scope_name"]
            count = len(rows) if stype == "industry" else (
                sub_counts.get(sname, 0) if stype == "sub_industry" else theme_counts.get(sname, 0)
            )
            cur.execute("""
                INSERT INTO sector_theme_insights (
                    scope_type, scope_name, generated_date, filing_count, sentiment,
                    headline, key_drivers, pricing_and_margins, capex_and_supply_chain, top_tickers
                ) VALUES (%s, %s, CURRENT_DATE, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (scope_type, scope_name) DO UPDATE SET
                    generated_date = CURRENT_DATE,
                    filing_count = EXCLUDED.filing_count,
                    sentiment = EXCLUDED.sentiment,
                    headline = EXCLUDED.headline,
                    key_drivers = EXCLUDED.key_drivers,
                    pricing_and_margins = EXCLUDED.pricing_and_margins,
                    capex_and_supply_chain = EXCLUDED.capex_and_supply_chain,
                    top_tickers = EXCLUDED.top_tickers;
            """, (
                stype, sname, count, item["sentiment"], item["headline"],
                item["key_drivers"], item["pricing_and_margins"],
                item["capex_and_supply_chain"], item["top_tickers"],
            ))
    conn.commit()
    print(f"Upserted {len(parsed['insights'])} industry, sub-industry, and theme insights.")


if __name__ == "__main__":
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        seed_sensitivity_map(conn)
        process_sec_filings(conn, limit_tickers=8)
        process_international_briefs(conn, limit_tickers=5)
        backfill_existing_filing_drivers(conn, limit_tickers=8)
        try:
            generate_market_macro_narrative(conn)
            generate_scoped_insights(conn)
        except Exception as e:
            print(f"Skipping synthesis due to API error: {e}")
    print(f"AI Filings & Macro Synthesis pipeline finished ({requests_used} Gemini calls, {groq_requests_used} Groq calls used).")
