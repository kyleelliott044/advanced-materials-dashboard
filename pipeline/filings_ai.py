import os
import re
import time
import json
import requests
import psycopg
import yfinance as yf
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field
from typing import List
from google import genai

DATABASE_URL = os.environ["DATABASE_URL"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

# SEC EDGAR requires a descriptive User-Agent header with contact email
SEC_HEADERS = {
    "User-Agent": "AdvancedMaterialsResearchPlatform research@materials-macro.io",
    "Accept-Encoding": "gzip, deflate",
}

client = genai.Client(api_key=GEMINI_API_KEY)

# Sub-sector to Commodity / Macro Input Sensitivity Map
SENSITIVITY_RULES = [
    # Industrial Gases
    ("Industrial Gases", "NG=F", "Cost Input", "Pass-Through", "Natural gas is primary feedstock for hydrogen/syngas; indexed via take-or-pay pass-through clauses."),
    ("Industrial Gases", "INDPRO", "Revenue Proxy", "Direct (Demand)", "Base merchant gas volumes track industrial manufacturing utilization."),
    # Specialty Alloys & Titanium
    ("Specialty Alloys", "ITA", "Revenue Proxy", "Direct (Demand)", "Commercial jet engine & airframe build rates drive multi-year vacuum-melted alloy backlogs."),
    ("Specialty Alloys", "WPU101", "Cost Input", "Pass-Through", "Nickel, cobalt, and titanium scrap surcharges pass through with a 1-2 quarter lag."),
    ("Specialty Alloys", "URA", "Revenue Proxy", "Direct (Demand)", "Nuclear fuel cladding and SMR zirconium/nickel alloy demand."),
    ("Aerospace & Defense Parts", "ITA", "Revenue Proxy", "Direct (Demand)", "Aftermarket PMA parts and defense spares scale with global flight hours."),
    ("Beryllium & Specialty Alloys", "SMH", "Revenue Proxy", "Direct (Demand)", "Tantalum/precious metal targets and beryllium optics tied to wafer fab & defense spend."),
    ("Aerospace & Auto Aluminum", "ALI=F", "Cost Input", "Pass-Through", "LME aluminum price passes through; conversion spreads depend on aerospace plate tightness."),
    ("Titanium Sponge & Metal", "ITA", "Revenue Proxy", "Direct (Demand)", "Aerospace-grade titanium sponge supply tightness drives multi-year OEM contract pricing."),
    ("Titanium Metal & Powders", "ITA", "Revenue Proxy", "Direct (Demand)", "Additive manufacturing titanium powders for defense and aerospace."),
    ("Stainless & Specialty Steel", "SLX", "Revenue Proxy", "Direct (Demand)", "Benchmark steel/alloy pricing sets base conversion margins."),
    ("Stainless & High-Perf Alloys", "HG=F", "Revenue Proxy", "Direct (Demand)", "Electrification, LNG, and chemical processing capex drive high-nickel alloy orders."),
    ("Advanced Stainless & Heating Alloys", "HG=F", "Revenue Proxy", "Direct (Demand)", "Industrial electric heating elements benefit from industrial decarbonization."),
    ("Specialty Steel & Alloys", "SLX", "Revenue Proxy", "Direct (Demand)", "Clean bar and seamless mechanical tubing tied to industrial/defense capex."),
    # Semiconductor & Electronic Materials
    ("Semiconductor Materials", "SMH", "Revenue Proxy", "Direct (Demand)", "Wafer starts and logic/memory node transitions drive filtration and deposition chemical consumption."),
    ("Silicon Wafers", "SMH", "Revenue Proxy", "Direct (Demand)", "300mm wafer shipments governed by long-term LTA take-or-pay volume commitments."),
    ("Silicon Wafers & PVC", "SMH", "Revenue Proxy", "Direct (Demand)", "300mm silicon wafer utilization tracks global fab output; PVC tied to construction/rates."),
    ("Engineered SOI Wafers", "SMH", "Revenue Proxy", "Direct (Demand)", "RF-SOI and FD-SOI wafers scale with 5G smartphone RF content and edge AI."),
    ("Photoresists & Semicon Chem", "SMH", "Revenue Proxy", "Direct (Demand)", "EUV and ArF immersion photoresists scale directly with leading-edge lithography layers."),
    ("Semicon Packaging & Chemicals", "SMH", "Revenue Proxy", "Direct (Demand)", "HBM and advanced 2.5D/3D packaging epoxy/chemical demand."),
    ("IC Substrates & Ceramics", "SMH", "Revenue Proxy", "Direct (Demand)", "AI server GPU package substrates (ABF) drive high-margin mix expansion."),
    ("ABF Build-Up Film & Bio", "SMH", "Revenue Proxy", "Direct (Demand)", "Near-monopoly on Ajinomoto Build-up Film insulating layers for AI accelerators."),
    ("CMP Slurries & Wet Chemicals", "SMH", "Revenue Proxy", "Direct (Demand)", "More CMP planarization steps per wafer in 3D NAND and gate-all-around logic."),
    ("CMP Slurries & Abrasives", "SMH", "Revenue Proxy", "Direct (Demand)", "Ultra-pure colloidal silica and silicon carbide wafer polishing slurries."),
    ("Silicon Carbide Wafers", "LIT", "Revenue Proxy", "Direct (Demand)", "800V EV traction inverters and AI data center power supplies drive 200mm SiC substrate demand."),
    ("Compound Semicon & Photonics", "SMH", "Revenue Proxy", "Direct (Demand)", "800G/1.6T optical transceivers for AI data center clusters."),
    ("High-Speed CCL (AI Servers)", "HG=F", "Cost Input", "Pass-Through", "Copper foil and glass fabric costs pass through; ultra-low-loss M7/M8 resin grades drive AI server margins."),
    ("High-Frequency CCL", "HG=F", "Cost Input", "Pass-Through", "High-speed networking switch and AI accelerator PCB laminate demand."),
    ("Copper Clad Laminates", "HG=F", "Cost Input", "Pass-Through", "Copper foil price swings directly impact working capital and pass-through pricing."),
    # Rare Earths & Battery Materials
    ("Rare Earth Mining & Magnets", "REMX", "Revenue Proxy", "Direct (Demand)", "NdPr oxide pricing dictates mining EBITDA; EV traction motors and defense actuators drive magnet demand."),
    ("Rare Earth Oxides & Metals", "REMX", "Revenue Proxy", "Direct (Demand)", "Chinese quota allocations and NdPr spot prices govern upstream margins."),
    ("NdFeB Permanent Magnets", "REMX", "Cost Input", "Pass-Through", "NdPr metal cost passes through to EV/wind OEMs with value-add processing fee."),
    ("Lithium Chemicals", "LIT", "Revenue Proxy", "Direct (Demand)", "Lithium carbonate/hydroxide spot prices directly drive brine and spodumene conversion margins."),
    ("Lithium Refining & Batteries", "LIT", "Revenue Proxy", "Direct (Demand)", "Spodumene-to-hydroxide conversion spread determines refining profitability."),
    ("High-Nickel Cathodes", "LIT", "Cost Input", "Pass-Through", "Lithium and nickel metal prices are indexed to cell-maker contracts; processing margin is fixed per kg."),
    ("Cathodes & Synthetic Anodes", "LIT", "Cost Input", "Pass-Through", "Lithium pass-through pricing paired with IRA-compliant North American supply chain premiums."),
    ("Battery Electrolytes", "LIT", "Revenue Proxy", "Direct (Demand)", "LiPF6 salt spreads and global GWh battery cell production."),
    ("Elecfoil (Battery Copper Foil)", "HG=F", "Cost Input", "Pass-Through", "LME copper passes through; ultra-thin 4-6 micron foil processing fee drives margin."),
    ("Natural Graphite Anode Mat.", "LIT", "Revenue Proxy", "Direct (Demand)", "Ex-China spherical graphite qualification for North American EV anode supply."),
    ("Synthetic Graphite Anodes", "LIT", "Revenue Proxy", "Direct (Demand)", "Graphitization energy costs and IRA-compliant anode localization."),
    # Composites, Ceramics & Chemicals
    ("Aerospace Carbon Fiber Composites", "ITA", "Revenue Proxy", "Direct (Demand)", "Widebody (A350/787) and defense composite weight-per-aircraft drives multi-year volume."),
    ("Aerospace Carbon Fiber Composites", "NG=F", "Cost Input", "Inverse (Cost)", "PAN precursor carbonization ovens are energy-intensive; natural gas spikes compress margins."),
    ("Carbon Fiber & Advanced Films", "ITA", "Revenue Proxy", "Direct (Demand)", "Aerospace prepreg and pressure vessel tow demand."),
    ("Optical Fiber & Display Glass", "SMH", "Revenue Proxy", "Direct (Demand)", "AI data center high-density fiber interconnects and Gen 10.5 display glass utilization."),
    ("Integrated Chemicals & Materials", "NG=F", "Cost Input", "Inverse (Cost)", "European/global natural gas and naphtha cracker spreads dictate base chemical profitability."),
    ("Integrated Chemicals & Materials", "CL=F", "Cost Input", "Inverse (Cost)", "Crude-to-naphtha feedstock costs impact downstream polyolefin and polyurethane margins."),
    ("Olefins & Polyolefins", "CL=F", "Margin Spread", "Direct (Demand)", "US ethane-to-crude oil cost advantage widens North American polyethylene margins when oil rises."),
    ("Packaging & Specialty Plastics", "NG=F", "Cost Input", "Inverse (Cost)", "USGC ethane/natural gas feedstock costs vs. global polyethylene clearing prices."),
    ("Fluoropolymers & Titanium Tech", "SMH", "Revenue Proxy", "Direct (Demand)", "High-purity PFA fluoropolymers essential for semiconductor fab piping and chemical handling."),
]

# Pydantic Schemas for Guaranteed Structured Gemini Outputs
class FilingSummarySchema(BaseModel):
    headline: str = Field(description="Concise 1-sentence institutional headline summarizing the filing/earnings.")
    executive_summary: str = Field(description="3-4 sentence analytical summary of revenue drivers, volume trends, and strategic positioning.")
    theme_tags: List[str] = Field(description="2 to 5 tags from: ['AI Infrastructure', 'Aerospace & Defense', 'Electrification & Grid', 'EV & Batteries', 'Semiconductors', 'Industrial Recovery', 'Commodity Headwind', 'Pricing Power']")
    pricing_and_margins: str = Field(description="Specific details on gross/operating margins, raw material pass-through, surcharges, or price/cost spread.")
    capex_and_backlog: str = Field(description="Details on order backlog, customer qualification milestones, capacity expansion, or capex guidance.")
    key_risks: str = Field(description="Top 1-2 operational, geopolitical, or cyclical risks highlighted in the report.")

class MacroNarrativeSchema(BaseModel):
    overall_regime: str = Field(description="Short 4-7 word regime label, e.g. 'AI & Aerospace Boom vs. Chemical Destocking'")
    executive_brief: str = Field(description="4-5 sentence institutional macro synthesis across the advanced materials value chain.")
    ai_theme_summary: str = Field(description="3-4 sentences synthesizing what filings say about AI data centers, HBM, substrates, optical fiber, and power.")
    aerospace_theme_summary: str = Field(description="3-4 sentences synthesizing commercial aero build rates, titanium/alloy bottlenecks, composites, and defense.")
    commodities_theme_summary: str = Field(description="3-4 sentences connecting current copper, natural gas, oil, lithium, and rare earth prices to company margins.")
    electrification_battery_summary: str = Field(description="3-4 sentences synthesizing EV battery destocking, grid transformers, nuclear/uranium, and rare earth magnets.")


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

    # Locate MD&A section ("Management's Discussion and Analysis") if present, otherwise take core body slice
    mda_match = re.search(r"(Item\s+2\.|Item\s+7\.)\s+Management.{0,30}Discussion", text, re.IGNORECASE)
    if mda_match:
        start = mda_match.start()
        return text[start : start + 28000]
    return text[5000:33000]


def process_sec_filings(conn, limit_tickers=35):
    """Fetches last 3 10-K/10-Q filings for US-listed tickers in equities_universe and summarizes with Gemini."""
    sec_map = fetch_sec_ticker_map()
    with conn.cursor() as cur:
        cur.execute("SELECT ticker, company_name, sub_sector FROM equities_universe ORDER BY qualification_years DESC NULLS LAST, ticker ASC;")
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
            for i, form in enumerate(recent["form"]):
                if form in ("10-K", "10-Q") and filings_found < 3:
                    f_date = recent["filingDate"][i]
                    acc_num = recent["accessionNumber"][i]
                    doc_name = recent["primaryDocument"][i]
                    source_url = f"https://www.sec.gov/Archives/edgar/data/{cik_unpadded}/{acc_num.replace('-', '')}/{doc_name}"

                    # Skip if already summarized in Neon
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
                    Extract concrete operational insights on demand trends, pricing power/raw material pass-through, backlog/capex, and risks.

                    FILING EXCERPT:
                    {excerpt}
                    """

                    ai_resp = client.models.generate_content(
                        model="gemini-2.5-flash",
                        contents=prompt,
                        config={
                            "response_mime_type": "application/json",
                            "response_schema": FilingSummarySchema,
                            "temperature": 0.2,
                        },
                    )
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
                    filings_found += 1
                    print(f"Summarized {ticker} {form} ({f_date})")
                    time.sleep(0.25)  # Respect SEC EDGAR 10 req/sec fair access limit

            processed += 1
        except Exception as e:
            print(f"Warning processing SEC filings for {ticker}: {e}")


def process_international_briefs(conn, limit_tickers=25):
    """Generates Earnings/Market Briefs for non-US tickers (Japan, Korea, Taiwan, Europe) using yfinance metadata & news."""
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

            prompt = f"""
            You are an institutional advanced materials equity analyst covering global equities.
            Synthesize a structured research & earnings brief for {company_name} ({ticker}), Sub-sector: {sub_sector}, Contract Type: {contract_type}, Qualification Moat: {qual_years} yrs.
            Financial Metrics: Revenue Growth={rev_growth}, Gross Margin={gross_margin}, EBITDA Margin={ebitda_margin}.
            Business Profile: {biz_summary}
            Recent News Headlines:
            {headlines_str}
            """

            ai_resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": FilingSummarySchema,
                    "temperature": 0.2,
                },
            )
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
            print(f"Generated global brief for {ticker}")
            time.sleep(0.3)
        except Exception as e:
            print(f"Warning generating international brief for {ticker}: {e}")


def generate_market_macro_narrative(conn):
    """Synthesizes all recent company filing summaries + live commodity prices into an overarching market narrative."""
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

    comm_text = "\n".join([f"- {c[1]} ({c[0]}): {c[2]} {c[3]} (as of {c[4]})" for c in commodities])
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

    ai_resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_schema": MacroNarrativeSchema,
            "temperature": 0.25,
        },
    )
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


if __name__ == "__main__":
    with psycopg.connect(DATABASE_URL) as conn:
        seed_sensitivity_map(conn)
        process_sec_filings(conn, limit_tickers=35)
        process_international_briefs(conn, limit_tickers=25)
        generate_market_macro_narrative(conn)
    print("AI Filings & Macro Synthesis pipeline finished.")
