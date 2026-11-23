import os
import io
import pandas as pd
import yfinance as yf
import requests
import psycopg

DATABASE_URL = os.environ["DATABASE_URL"]

# 142 Unique Global Advanced Materials Equities
# Schema: (ticker, company_name, sub_sector, contract_type, qualification_years)
EQUITIES_UNIVERSE = [
    # --- Industrial Gases ---
    ("LIN", "Linde Plc", "Industrial Gases", "take_or_pay", 3.0),
    ("AI.PA", "Air Liquide", "Industrial Gases", "take_or_pay", 3.0),
    ("4088.T", "Air Water Inc.", "Industrial Gases", "take_or_pay", 2.5),

    # --- Specialty Alloys, Metals & Titanium ---
    ("CRS", "Carpenter Technology", "Specialty Alloys", "pass_through", 5.0),
    ("ATI", "ATI Inc.", "Specialty Alloys", "pass_through", 5.0),
    ("HEI", "Heico Corporation", "Aerospace & Defense Parts", "fixed_spread", 4.5),
    ("MTRN", "Materion Corporation", "Beryllium & Specialty Alloys", "pass_through", 4.0),
    ("APAM.AS", "Aperam", "Stainless & Specialty Steel", "pass_through", 2.5),
    ("ACX.MC", "Acerinox", "Stainless & High-Perf Alloys", "pass_through", 2.5),
    ("ALLEI.ST", "Alleima", "Advanced Stainless & Heating Alloys", "pass_through", 3.5),
    ("MTUS", "Metallus Inc.", "Specialty Steel & Alloys", "pass_through", 3.0),
    ("CSTM", "Constellium", "Aerospace & Auto Aluminum", "pass_through", 3.5),
    ("5726.T", "Osaka Titanium Technologies", "Titanium Sponge & Metal", "take_or_pay", 5.0),
    ("IPX", "IperionX Limited", "Titanium Metal & Powders", "fixed_spread", 3.5),
    ("KMT", "Kennametal Inc.", "Tungsten Carbide & Tooling", "fixed_spread", 2.0),

    # --- Electronic & Semiconductor Materials ---
    ("ENTG", "Entegris", "Semiconductor Materials", "fixed_spread", 3.5),
    ("4063.T", "Shin-Etsu Chemical", "Silicon Wafers & PVC", "take_or_pay", 4.0),
    ("3436.T", "SUMCO Corporation", "Silicon Wafers", "take_or_pay", 4.0),
    ("6488.TWO", "GlobalWafers", "Silicon Wafers", "take_or_pay", 4.0),
    ("WAF.DE", "Siltronic AG", "Silicon Wafers", "take_or_pay", 4.0),
    ("SOI.PA", "Soitec", "Engineered SOI Wafers", "take_or_pay", 4.5),
    ("4186.T", "Tokyo Ohka Kogyo", "Photoresists & Semicon Chem", "fixed_spread", 4.0),
    ("4004.T", "Resonac Holdings", "Semicon Packaging & Chemicals", "fixed_spread", 3.5),
    ("4062.T", "Ibiden Co., Ltd.", "IC Substrates & Ceramics", "fixed_spread", 3.5),
    ("2802.T", "Ajinomoto Co., Inc.", "ABF Build-Up Film & Bio", "fixed_spread", 4.5),
    ("6988.T", "Nitto Denko", "Optical Films & Semicon Tapes", "fixed_spread", 3.0),
    ("4980.T", "Dexerials Corporation", "Anisotropic Conductive Films", "fixed_spread", 3.5),
    ("4203.T", "Sumitomo Bakelite", "Epoxy Molding Compounds", "fixed_spread", 3.0),
    ("6890.T", "Ferrotec Holdings", "Semicon Vacuum & Quartz", "fixed_spread", 3.0),
    ("688019.SS", "Anji Microelectronics", "CMP Slurries & Wet Chemicals", "fixed_spread", 3.5),
    ("4021.T", "Nissan Chemical", "Semicon Bottom Anti-Reflective", "fixed_spread", 3.5),
    ("5384.T", "Fujimi Incorporated", "CMP Slurries & Abrasives", "fixed_spread", 4.0),
    ("357780.KQ", "Soulbrain Co., Ltd.", "Etchants & Semicon Electrolytes", "fixed_spread", 3.5),
    ("005290.KQ", "Dongjin Semichem", "Photoresists & Wet Chemicals", "fixed_spread", 3.5),
    ("4103.T", "Stella Chemifa", "High-Purity Hydrofluoric Acid", "fixed_spread", 4.0),
    ("WOLF", "Wolfspeed Inc.", "Silicon Carbide Wafers", "take_or_pay", 4.5),
    ("COHR", "Coherent Corp.", "Compound Semicon & Photonics", "fixed_spread", 3.5),
    ("OLED", "Universal Display Corp.", "OLED Emitters & Materials", "fixed_spread", 4.0),
    ("ESI", "Element Solutions Inc", "Electronics & PCB Chemistry", "fixed_spread", 2.5),
    ("DD", "DuPont de Nemours", "Electronics & Industrial Mat.", "fixed_spread", 3.0),
    ("QNTY", "Qnity Electronics Inc", "Semicon & Interconnect Mat.", "fixed_spread", 3.0),
    ("SOLS", "Solstice Advanced Mat.", "Electronic & Specialty Mat.", "fixed_spread", 3.0),
    ("ROG", "Rogers Corporation", "RF/Microwave Laminates", "fixed_spread", 3.5),
    ("CTS", "CTS Corporation", "Piezoelectric & Electronic Comp.", "fixed_spread", 3.0),

    # --- Copper Clad Laminates (CCL) & PCB Substrates ---
    ("2383.TW", "Elite Material Co.", "High-Speed CCL (AI Servers)", "fixed_spread", 3.0),
    ("6274.TWO", "Taiwan Union Technology", "High-Frequency CCL", "fixed_spread", 3.0),
    ("6213.TW", "Iteq Corporation", "Copper Clad Laminates", "fixed_spread", 2.5),
    ("1888.HK", "Kingboard Laminates", "Copper Clad Laminates", "pass_through", 1.5),
    ("1303.TW", "Nan Ya Plastics Corp", "Electronic Mat. & Plastics", "pass_through", 2.0),
    ("8070.TWO", "Chang Wah Technology", "Leadframes & Packaging Mat.", "fixed_spread", 2.5),

    # --- Rare Earths & Permanent Magnets ---
    ("MP", "MP Materials", "Rare Earth Mining & Magnets", "pass_through", 4.0),
    ("600111.SS", "China Northern Rare Earth", "Rare Earth Oxides & Metals", "pass_through", 3.0),
    ("600392.SS", "Shenghe Resources", "Rare Earth Refining", "pass_through", 3.0),
    ("300748.SZ", "JL Mag Rare-Earth", "NdFeB Permanent Magnets", "pass_through", 3.5),
    ("000795.SZ", "Ningbo Yunsheng Co., Ltd.", "NdFeB Permanent Magnets", "pass_through", 3.0),
    ("300224.SZ", "Yantai Zhenghai Magnetic", "NdFeB Permanent Magnets", "pass_through", 3.0),
    ("NEO.TO", "Neo Performance Materials", "Rare Earth Separation & Magnets", "pass_through", 3.5),

    # --- Battery Materials (Lithium, Cathodes, Foils, Graphite) ---
    ("ALB", "Albemarle Corporation", "Lithium Chemicals", "pass_through", 3.0),
    ("002460.SZ", "Ganfeng Lithium", "Lithium Refining & Batteries", "pass_through", 3.0),
    ("5713.T", "Sumitomo Metal Mining", "Nickel/Cobalt Cathodes & Copper", "pass_through", 3.5),
    ("051910.KS", "LG Chem", "Cathodes & Petrochemicals", "pass_through", 3.0),
    ("006400.KS", "Samsung SDI", "EV Batteries & Electronic Mat.", "fixed_spread", 3.5),
    ("003670.KS", "POSCO Future M", "Cathodes & Synthetic Anodes", "pass_through", 3.5),
    ("247540.KQ", "Ecopro BM", "High-Nickel Cathodes", "pass_through", 3.5),
    ("066970.KQ", "L&F Co., Ltd.", "NCMA Cathode Materials", "pass_through", 3.5),
    ("005420.KS", "Cosmo Advanced Materials", "Cathode Active Materials", "pass_through", 3.0),
    ("300037.SZ", "Capchem Technology", "Battery Electrolytes & Fluorochem", "fixed_spread", 3.0),
    ("348370.KQ", "Enchem Co., Ltd.", "Battery Electrolytes", "fixed_spread", 3.0),
    ("011790.KS", "SKC Co., Ltd.", "Copper Foil & Semicon Substrates", "fixed_spread", 3.0),
    ("020150.KS", "Lotte Energy Materials", "Elecfoil (Battery Copper Foil)", "fixed_spread", 3.0),
    ("336370.KS", "Solus Advanced Materials", "Copper Foil & OLED Materials", "fixed_spread", 3.0),
    ("SYR.AX", "Syrah Resources", "Natural Graphite Anode Mat.", "take_or_pay", 3.5),
    ("NVX.AX", "Novonix Limited", "Synthetic Graphite Anodes", "take_or_pay", 4.0),
    ("GRA.TO", "NanoXplore Inc.", "Graphene & Composite Mat.", "fixed_spread", 2.5),
    ("FEAM", "5E Advanced Materials", "Boron & Lithium Specialty", "fixed_spread", 3.0),
    ("UMI.BR", "Umicore", "Battery Recycling & Catalysts", "pass_through", 3.0),
    ("JMAT.L", "Johnson Matthey", "PGM Refining & Hydrogen Cat.", "pass_through", 3.5),

    # --- Technical Ceramics, Glass, Carbon & Refractories ---
    ("GLW", "Corning Inc", "Optical Fiber & Display Glass", "fixed_spread", 3.5),
    ("5201.T", "AGC Inc.", "Architectural, Auto & Semicon Glass", "fixed_spread", 3.0),
    ("5214.T", "Nippon Electric Glass", "Specialty & Substrate Glass", "fixed_spread", 3.0),
    ("6971.T", "Kyocera Corporation", "Fine Ceramics & Semicon Pkg", "fixed_spread", 3.5),
    ("5333.T", "NGK Insulators", "Technical Ceramics & Substrates", "fixed_spread", 3.5),
    ("5344.T", "Maruwa Co., Ltd.", "Ceramic Substrates (EV/5G)", "fixed_spread", 3.5),
    ("300285.SZ", "Shandong Sinocera", "MLCC Powders & Functional Ceram.", "fixed_spread", 3.5),
    ("MGAM.L", "Morgan Advanced Materials", "Advanced Carbon & Ceramics", "fixed_spread", 3.0),
    ("VSVS.L", "Vesuvius plc", "Molten Metal Flow & Refractories", "fixed_spread", 2.5),
    ("RHIM.L", "RHI Magnesita", "Industrial Refractories", "pass_through", 2.0),
    ("5301.T", "Tokai Carbon", "Graphite Electrodes & Carbon Black", "pass_through", 2.5),
    ("5310.T", "Toyo Tanso", "Isotropic Graphite (Semicon/Nuc)", "fixed_spread", 4.0),
    ("SGL.DE", "SGL Carbon", "Specialty Graphite & Carbon Fiber", "fixed_spread", 3.5),
    ("MRSN.PA", "Mersen", "Electrical Power & Graphite", "fixed_spread", 3.5),
    ("CBT", "Cabot Corporation", "Conductive Carbons & Carbon Black", "pass_through", 2.5),

    # --- Advanced Composites, Carbon Fiber & Technical Textiles ---
    ("HXL", "Hexcel Corporation", "Aerospace Carbon Fiber Composites", "take_or_pay", 5.0),
    ("3402.T", "Toray Industries", "Carbon Fiber & Advanced Films", "fixed_spread", 4.5),
    ("3401.T", "Teijin Limited", "Aramid & Carbon Fibers", "fixed_spread", 3.5),
    ("298050.KS", "Hyosung Advanced Materials", "Carbon Fiber & Tire Cord", "fixed_spread", 3.5),
    ("120290.KS", "Kolon Industries", "Aramid Fibers & Industrial Films", "fixed_spread", 3.0),
    ("AIN", "Albany International", "Engineered Composites & Fabrics", "fixed_spread", 4.5),
    ("GURN.SW", "Gurit Holding", "Wind & Marine Composites", "fixed_spread", 2.5),
    ("ASPN", "Aspen Aerogels", "Thermal Barrier Aerogels", "fixed_spread", 3.5),

    # --- High-Performance Polymers & Specialty Chemicals ---
    ("MMM", "3M Company", "Diversified Advanced Materials", "fixed_spread", 3.0),
    ("MRK.DE", "Merck KGaA", "Electronics & Life Science Mat.", "fixed_spread", 3.5),
    ("BAS.DE", "BASF SE", "Integrated Chemicals & Materials", "pass_through", 2.0),
    ("DOW", "Dow Inc", "Packaging & Specialty Plastics", "pass_through", 1.5),
    ("LYB", "LyondellBasell", "Olefins & Polyolefins", "pass_through", 1.5),
    ("1COV.DE", "Covestro AG", "Polyurethanes & Polycarbonates", "pass_through", 2.0),
    ("EVK.DE", "Evonik Industries", "Specialty Additives & Polymers", "fixed_spread", 2.5),
    ("SYENS.BR", "Syensqo", "Specialty Polymers & Composites", "fixed_spread", 4.0),
    ("AKE.PA", "Arkema", "Adhesives & Advanced Materials", "fixed_spread", 3.0),
    ("VCT.L", "Victrex plc", "PEEK High-Performance Polymers", "fixed_spread", 4.5),
    ("CE", "Celanese Corporation", "Engineered Materials & Acetyls", "pass_through", 2.5),
    ("EMN", "Eastman Chemical", "Specialty Plastics & Additives", "fixed_spread", 2.5),
    ("HUN", "Huntsman Corporation", "Polyurethanes & Epoxy Systems", "pass_through", 2.0),
    ("CC", "The Chemours Company", "Fluoropolymers & Titanium Tech", "pass_through", 3.0),
    ("WCH.DE", "Wacker Chemie", "Polysilicon & Silicones", "take_or_pay", 3.5),
    ("ELK.OL", "Elkem ASA", "Silicones & Silicon Materials", "pass_through", 2.5),
    ("3407.T", "Asahi Kasei", "Separators, Polymers & Healthcare", "fixed_spread", 3.0),
    ("4005.T", "Sumitomo Chemical", "Agro, Petrochem & IT Materials", "pass_through", 2.5),
    ("4188.T", "Mitsubishi Chemical Group", "Specialty Polymers & Ind. Gases", "pass_through", 2.5),
    ("4183.T", "Mitsui Chemicals", "Mobility & ICT Polymers", "pass_through", 2.5),
    ("3405.T", "Kuraray Co., Ltd.", "PVOH Resins & Elastomers", "fixed_spread", 3.0),
    ("4202.T", "Daicel Corporation", "Cellulose & Safety Inflators", "fixed_spread", 3.0),
    ("4118.T", "Kaneka Corporation", "Modified Silicones & Biopolymers", "fixed_spread", 2.5),
    ("4208.T", "UBE Corporation", "Polyimides & Nylon", "fixed_spread", 3.0),
    ("4205.T", "Zeon Corporation", "Cyclo Olefin Polymers & Elastomers", "fixed_spread", 3.5),
    ("4631.T", "DIC Corporation", "Pigments, Inks & PPS Compounds", "fixed_spread", 2.5),
    ("1717.TW", "Eternal Materials", "Resins & Electronic Materials", "fixed_spread", 2.5),
    ("CLN.SW", "Clariant AG", "Catalysts & Care Chemicals", "fixed_spread", 2.5),
    ("CRDA.L", "Croda International", "Life Science & Consumer Actives", "fixed_spread", 3.0),
    ("ASH", "Ashland Inc.", "Cellulosics & Pharma Excipients", "fixed_spread", 3.0),
    ("FUL", "H.B. Fuller", "Industrial Adhesives", "fixed_spread", 2.0),
    ("AVNT", "Avient Corporation", "Colorants & Specialty Formulations", "fixed_spread", 2.0),
    ("NGVT", "Ingevity Corporation", "Activated Carbon & Specialty Chem", "fixed_spread", 2.5),
    ("MTX", "Minerals Technologies", "Precipitated Calcium & Bentonite", "fixed_spread", 2.0),
    ("IOSP", "Innospec Inc.", "Fuel & Performance Specialties", "fixed_spread", 2.5),
    ("ECVT", "Ecovyst Inc.", "Sulfuric Acid & Silica Catalysts", "take_or_pay", 3.0),
    ("KOP", "Koppers Holdings", "Wood Preservation & Carbon Pitch", "pass_through", 2.0),
    ("RYAM", "Rayonier Advanced Materials", "High-Purity Cellulose", "fixed_spread", 3.0),
    ("500380.NS", "SRF Limited", "Fluorochemicals & Technical Textiles", "fixed_spread", 3.0),

    # --- Construction, Coatings & Biomaterials ---
    ("SGO.PA", "Compagnie de Saint-Gobain", "High-Performance Building Mat.", "fixed_spread", 2.0),
    ("SIKA.SW", "Sika AG", "Specialty Construction Chemicals", "fixed_spread", 2.5),
    ("PPG", "PPG Industries", "Aerospace & Industrial Coatings", "fixed_spread", 2.5),
    ("AVTR", "Avantor Inc.", "Ultra-Pure Lab & Biopharma Mat.", "fixed_spread", 3.5),
    ("CRBN.AS", "Corbion", "Lactic Acid & Biopolymers", "fixed_spread", 3.0),
    ("IART", "Integra LifeSciences", "Regenerative Biomaterials", "fixed_spread", 4.5),
    ("ANIK", "Anika Therapeutics", "Hyaluronic Acid Biomaterials", "fixed_spread", 4.0),
]

# Market Commodities & Downstream ETFs (Yahoo Finance)
MARKET_INPUTS = [
    ("HG=F", "Copper Futures", "USD/lb", "Upstream Metals"),
    ("ALI=F", "Aluminum Futures", "USD/mt", "Upstream Metals"),
    ("PA=F", "Palladium Futures", "USD/oz", "Precious / PGM"),
    ("PL=F", "Platinum Futures", "USD/oz", "Precious / PGM"),
    ("NG=F", "Henry Hub Natural Gas", "USD/MMBtu", "Energy & Feedstock"),
    ("CL=F", "WTI Crude Oil", "USD/bbl", "Energy & Feedstock"),
    ("REMX", "Rare Earth & Strategic Metals ETF", "USD", "Strategic Materials"),
    ("SLX", "VanEck Steel ETF", "USD", "Upstream Metals"),
    ("SMH", "VanEck Semiconductor ETF", "USD", "Downstream Secular"),
    ("LIT", "Global X Lithium & Battery ETF", "USD", "Downstream Secular"),
    ("URA", "Global X Uranium ETF", "USD", "Downstream Secular"),
    ("ITA", "iShares U.S. Aerospace & Defense ETF", "USD", "Downstream Secular"),
]

# Macroeconomic Series (FRED Direct CSV)
FRED_INPUTS = [
    ("INDPRO", "US Industrial Production Index", "Index 2017=100", "Macro Activity"),
    ("PCUOMFGOMFG", "PPI Total Manufacturing", "Index Dec 1984=100", "Input Inflation"),
    ("WPU101", "PPI Iron and Steel", "Index 1982=100", "Input Inflation"),
    ("WPU066", "PPI Plastic Resins & Materials", "Index 1982=100", "Input Inflation"),
    ("PCOBAUSDM", "Global Cobalt Price", "USD / Metric Ton", "Battery & Alloy Metal"),
    ("PNICKUSDM", "Global Nickel Price", "USD / Metric Ton", "Battery & Alloy Metal"),
    ("DGS10", "10-Year Treasury Constant Maturity", "Percent", "Cost of Capital"),
]

def seed_equities(conn):
    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO equities_universe (ticker, company_name, sub_sector, contract_type, qualification_years)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (ticker) DO UPDATE SET
                company_name = EXCLUDED.company_name,
                sub_sector = EXCLUDED.sub_sector,
                contract_type = EXCLUDED.contract_type,
                qualification_years = EXCLUDED.qualification_years;
        """, EQUITIES_UNIVERSE)
    print(f"Upserted {len(EQUITIES_UNIVERSE)} equities into equities_universe.")

def ingest_market_inputs(conn):
    records = []
    for symbol, name, unit, category in MARKET_INPUTS:
        try:
            hist = yf.Ticker(symbol).history(period="5d")
            if not hist.empty:
                latest_date = hist.index[-1].date()
                latest_val = float(hist["Close"].iloc[-1])
                records.append((symbol, name, category, unit, latest_date, latest_val))
        except Exception as e:
            print(f"Warning: failed to fetch {symbol}: {e}")

    with conn.cursor() as cur:
        for symbol, name, category, unit, obs_date, val in records:
            cur.execute("""
                INSERT INTO raw_inputs (input_code, input_name, category, unit, observation_date, value)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (input_code, observation_date) DO UPDATE SET
                    value = EXCLUDED.value,
                    input_name = EXCLUDED.input_name,
                    category = EXCLUDED.category,
                    unit = EXCLUDED.unit;
            """, (symbol, name, category, unit, obs_date, val))
    print(f"Ingested {len(records)} market commodity/ETF inputs into raw_inputs.")

def ingest_fred_inputs(conn):
    records = []
    for series_id, name, unit, category in FRED_INPUTS:
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            df = pd.read_csv(io.StringIO(resp.text))
            df.columns = ["DATE", "VALUE"]
            df = df[df["VALUE"] != "."].dropna()
            if not df.empty:
                latest_row = df.iloc[-1]
                obs_date = pd.to_datetime(latest_row["DATE"]).date()
                val = float(latest_row["VALUE"])
                records.append((series_id, name, category, unit, obs_date, val))
        except Exception as e:
            print(f"Warning: failed to fetch FRED series {series_id}: {e}")

    with conn.cursor() as cur:
        for series_id, name, category, unit, obs_date, val in records:
            cur.execute("""
                INSERT INTO raw_inputs (input_code, input_name, category, unit, observation_date, value)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (input_code, observation_date) DO UPDATE SET
                    value = EXCLUDED.value,
                    input_name = EXCLUDED.input_name,
                    category = EXCLUDED.category,
                    unit = EXCLUDED.unit;
            """, (series_id, name, category, unit, obs_date, val))
    print(f"Ingested {len(records)} FRED macro series into raw_inputs.")

if __name__ == "__main__":
    with psycopg.connect(DATABASE_URL) as conn:
        seed_equities(conn)
        ingest_market_inputs(conn)
        ingest_fred_inputs(conn)
        conn.commit()
    print("Daily Materials & Equity ETL completed successfully.")
