import { CompanyProduct } from "./productRollup";

export interface AtlasCompanyLink {
  ticker: string;
  companyName: string;
  subSector: string | null;
  country: string | null;
  marketCapUsd: number | null;
  contractType: string | null;
  linkedProducts: {
    id?: string | number;
    name: string;
    category?: string;
    businessUnit?: string;
    detail?: string;
    endDemand?: string;
  }[];
  sourceOrigin: "product" | "filing_driver" | "sector_rule";
}

export interface MaterialAtlasEntry {
  slug: string;
  kind: "raw" | "advanced";
  name: string;
  categoryLabel: string;
  registryCode: string | null;
  spotPriceLabel: string | null;
  summary: string;
  endMarkets: string[];
  companies: AtlasCompanyLink[];
}

interface MinimalEquity {
  ticker: string;
  company_name: string;
  sub_sector: string | null;
  contract_type: string | null;
  country: string | null;
  market_cap_usd: number | null;
}

interface MinimalInput {
  input_code: string;
  input_name: string;
  category: string | null;
  unit: string | null;
  value: string | number;
}

interface MinimalDriver {
  ticker: string;
  commodity_name: string;
  matched_input_code: string | null;
  exposure_role: string;
  impact_direction: string;
  mechanism_detail: string;
}

interface MinimalSensitivity {
  sub_sector: string;
  input_code: string;
  relationship_type: string;
  impact_direction: string;
  mechanism_note: string;
}

const INVALID_RAW_TOKENS_RE =
  /\b(labor|raw\s+materials?|direct\s+materials?|feedstocks?|intermediates?|specialty\s+chemicals?|industrial\s+chemicals?|base\s+chemicals?|commodit(y|ies)|precious\s+metals?|rare\s+earths?|plastic\s+resins?|polymers?|resins?|solvents?|industrial\s+gases?|energy|electricity|power|utilities|water|freight|logistics|transportation|shipping|distribution|packaging|consumables?|components?|parts?|sub-?assembl(y|ies)|equipment|machinery|tooling|scrap|general|other|various|miscellaneous|n\/?a)\b/i;

const DANGLING_CONJUNCTION_RE = /^(and|or|&|,|-)\b|\b(and|or|&|,|-)$/i;

const GENERIC_ADV_BUCKET_RE =
  /^(electronic\s+materials?|advanced\s+materials?(\s+portfolio)?|specialty\s+materials?|performance\s+chemicals?|engineered\s+materials?|industrial\s+materials?|semiconductor\s+materials?|battery\s+materials?)$/i;

const CANONICAL_CODE_NAMES: Record<string, string> = {
  "HG=F": "Copper",
  "NG=F": "Natural Gas",
  "CL=F": "WTI Crude Oil",
  "BZ=F": "Brent Crude Oil",
  "ALI=F": "Aluminum",
  "PL=F": "Platinum",
  "SI=F": "Silver",
  "PA=F": "Palladium",
  "GC=F": "Gold",
  PNICKUSDM: "Nickel",
  PCOBAUSDM: "Cobalt",
  PNGASEUUSDM: "Natural Gas",
  WPU101: "Iron & Steel",
  NDPR_CN: "Neodymium-Praseodymium (NdPr) Oxide",
  LICARB_CN: "Lithium Carbonate",
  GALLIUM_9999: "Gallium",
  GERMANIUM_INGOT: "Germanium",
  POLYSILICON_N: "Polysilicon",
  SPHERICAL_GRAPHITE: "Spherical Graphite",
  COBALT_SULFATE: "Cobalt Sulfate",
  CARBON_FIBER_T700: "Polyacrylonitrile (PAN) Precursor",
};

const RAW_MATERIAL_ALIASES: Record<string, string> = {
  copper: "Copper",
  "refined copper": "Copper",
  "copper cathode": "Copper",
  "copper cathodes": "Copper",
  "copper foil": "Copper",
  "natural gas": "Natural Gas",
  "european natural gas": "Natural Gas",
  "ttf natural gas": "Natural Gas",
  "liquefied natural gas": "Natural Gas",
  lng: "Natural Gas",
  "crude oil": "WTI Crude Oil",
  "wti crude": "WTI Crude Oil",
  "wti crude oil": "WTI Crude Oil",
  "brent crude": "Brent Crude Oil",
  "brent crude oil": "Brent Crude Oil",
  aluminum: "Aluminum",
  aluminium: "Aluminum",
  "primary aluminum": "Aluminum",
  nickel: "Nickel",
  "class 1 nickel": "Nickel",
  "nickel sulfate": "Nickel Sulfate",
  "nickel sulphate": "Nickel Sulfate",
  cobalt: "Cobalt",
  "cobalt sulfate": "Cobalt Sulfate",
  "cobalt sulphate": "Cobalt Sulfate",
  "cobalt hydroxide": "Cobalt Hydroxide",
  lithium: "Lithium Carbonate",
  "lithium carbonate": "Lithium Carbonate",
  "lithium hydroxide": "Lithium Hydroxide",
  spodumene: "Spodumene Concentrate",
  "spodumene concentrate": "Spodumene Concentrate",
  graphite: "Natural Flake Graphite",
  "natural graphite": "Natural Flake Graphite",
  "natural flake graphite": "Natural Flake Graphite",
  "spherical graphite": "Spherical Graphite",
  "needle coke": "Petroleum Needle Coke",
  "petroleum needle coke": "Petroleum Needle Coke",
  "calcined petroleum coke": "Calcined Petroleum Coke",
  "coal tar pitch": "Coal Tar Pitch",
  polysilicon: "Polysilicon",
  "polycrystalline silicon": "Polysilicon",
  "silicon metal": "Metallurgical Silicon",
  "metallurgical silicon": "Metallurgical Silicon",
  "metallurgical grade silicon": "Metallurgical Silicon",
  gallium: "Gallium",
  germanium: "Germanium",
  "germanium tetrachloride": "Germanium Tetrachloride",
  indium: "Indium",
  tantalum: "Tantalum",
  tungsten: "Tungsten",
  "tungsten concentrate": "Tungsten",
  molybdenum: "Molybdenum",
  titanium: "Titanium Sponge",
  "titanium sponge": "Titanium Sponge",
  "titanium scrap": "Titanium Scrap",
  ilmenite: "Ilmenite Ore",
  rutile: "Rutile Ore",
  "titanium tetrachloride": "Titanium Tetrachloride",
  beryllium: "Beryllium",
  "beryllium ore": "Beryllium",
  bertrandite: "Bertrandite Ore",
  zirconium: "Zirconium",
  hafnium: "Hafnium",
  platinum: "Platinum",
  palladium: "Palladium",
  rhodium: "Rhodium",
  iridium: "Iridium",
  ruthenium: "Ruthenium",
  silver: "Silver",
  gold: "Gold",
  ndpr: "Neodymium-Praseodymium (NdPr) Oxide",
  "ndpr oxide": "Neodymium-Praseodymium (NdPr) Oxide",
  "neodymium praseodymium": "Neodymium-Praseodymium (NdPr) Oxide",
  "neodymium praseodymium oxide": "Neodymium-Praseodymium (NdPr) Oxide",
  neodymium: "Neodymium",
  praseodymium: "Praseodymium",
  dysprosium: "Dysprosium",
  terbium: "Terbium",
  "rare earth concentrate": "Rare Earth Concentrate",
  bastnaesite: "Bastnaesite Concentrate",
  monazite: "Monazite Concentrate",
  ethane: "Ethane",
  ethylene: "Ethylene",
  propylene: "Propylene",
  naphtha: "Naphtha",
  benzene: "Benzene",
  toluene: "Toluene",
  xylene: "Xylene",
  phenol: "Phenol",
  acetone: "Acetone",
  methanol: "Methanol",
  ammonia: "Ammonia",
  "anhydrous ammonia": "Ammonia",
  chlorine: "Chlorine",
  "caustic soda": "Sodium Hydroxide (Caustic Soda)",
  "sodium hydroxide": "Sodium Hydroxide (Caustic Soda)",
  "hydrofluoric acid": "Hydrofluoric Acid",
  "anhydrous hydrogen fluoride": "Hydrogen Fluoride",
  "hydrogen fluoride": "Hydrogen Fluoride",
  fluorspar: "Fluorspar (Calcium Fluoride)",
  "sulfuric acid": "Sulfuric Acid",
  "sulphuric acid": "Sulfuric Acid",
  "phosphoric acid": "Phosphoric Acid",
  "nitric acid": "Nitric Acid",
  "hydrochloric acid": "Hydrochloric Acid",
  "colloidal silica": "Colloidal Silica",
  "fumed silica": "Fumed Silica",
  "quartz sand": "High-Purity Quartz Sand",
  "silica sand": "Silica Sand",
  alumina: "Alumina (Aluminum Oxide)",
  "aluminum oxide": "Alumina (Aluminum Oxide)",
  bauxite: "Bauxite Ore",
  magnesia: "Magnesia (Magnesium Oxide)",
  "magnesium oxide": "Magnesia (Magnesium Oxide)",
  magnesite: "Magnesite Ore",
  boron: "Boron",
  colemanite: "Colemanite Ore",
  "boric acid": "Boric Acid",
  "epoxy resin": "Epoxy Resin",
  "epoxy resins": "Epoxy Resin",
  bisphenol: "Bisphenol-A (BPA)",
  "bisphenol a": "Bisphenol-A (BPA)",
  epichlorohydrin: "Epichlorohydrin",
  acrylonitrile: "Acrylonitrile",
  polyacrylonitrile: "Polyacrylonitrile (PAN) Precursor",
  "pan precursor": "Polyacrylonitrile (PAN) Precursor",
  "wood pulp": "Dissolving Wood Pulp",
  "dissolving wood pulp": "Dissolving Wood Pulp",
  cellulose: "Dissolving Wood Pulp",
  "lactic acid": "Lactic Acid",
  "iron ore": "Iron Ore",
  "steel scrap": "Ferrous & Alloy Steel Scrap",
  "stainless steel scrap": "Stainless Steel Scrap",
  "nickel scrap": "Nickel Superalloy Scrap",
  "iron & steel": "Iron & Steel",
  "iron and steel": "Iron & Steel",
};

const ADV_MATERIAL_ALIASES: Record<string, string> = {
  "photoresists semicon chem": "Photoresists & Semiconductor Chemicals",
  "photoresists semiconductor chemicals": "Photoresists & Semiconductor Chemicals",
  "semicon packaging chemicals": "Semiconductor Packaging & Chemicals",
  "semiconductor packaging chemicals": "Semiconductor Packaging & Chemicals",
  "compound semicon photonics": "Compound Semiconductors & Photonics",
  "compound semiconductors photonics": "Compound Semiconductors & Photonics",
  "natural graphite anode mat": "Natural Graphite Anode Materials",
  "natural graphite anode materials": "Natural Graphite Anode Materials",
  "stainless high perf alloys": "Stainless & High-Performance Alloys",
  "stainless high performance alloys": "Stainless & High-Performance Alloys",
  "fluoropolymers titanium tech": "Fluoropolymers & Titanium Technologies",
  "fluoropolymers titanium technologies": "Fluoropolymers & Titanium Technologies",
  "copper clad laminates": "Copper Clad Laminates (CCL)",
  "high speed ccl": "High-Speed Copper Clad Laminates (CCL)",
  "high frequency ccl": "High-Frequency Copper Clad Laminates (CCL)",
  "silicon wafer": "Silicon Wafers",
  "silicon wafers": "Silicon Wafers",
  "silicon carbide wafer": "Silicon Carbide (SiC) Wafers",
  "silicon carbide wafers": "Silicon Carbide (SiC) Wafers",
  "sic wafers": "Silicon Carbide (SiC) Wafers",
  "cmp slurries": "CMP Slurries & Pads",
  "cmp slurries pads": "CMP Slurries & Pads",
  "cmp slurries wet chemicals": "CMP Slurries & Wet Chemicals",
  "ndfeb permanent magnets": "NdFeB Permanent Magnets",
  "rare earth permanent magnets": "NdFeB Permanent Magnets",
  "high nickel cathodes": "High-Nickel Cathode Materials",
  "high nickel cathode materials": "High-Nickel Cathode Materials",
  "cathode active materials": "High-Nickel Cathode Materials",
};

const PRESERVE_UPPER_TOKENS = new Set([
  "wti",
  "pan",
  "bpa",
  "lng",
  "pvc",
  "ptfe",
  "pfa",
  "peek",
  "pps",
  "mdi",
  "tdi",
  "hcl",
  "hf",
  "h2so4",
  "lipf6",
  "sic",
  "gan",
  "gaas",
  "inp",
  "sio2",
  "al2o3",
  "tio2",
  "ticl4",
  "gecl4",
  "ccl",
  "abf",
  "cmp",
  "euv",
  "arf",
  "oled",
  "mlcc",
  "soi",
  "hbm",
  "ic",
  "rf",
  "ev",
  "pgm",
  "pvoh",
  "ncma",
  "nmc",
  "lfp",
  "ndfeb",
]);

const MIXED_CASE_TOKENS: Record<string, string> = {
  ndpr: "NdPr",
  "(ndpr)": "(NdPr)",
  "(bpa)": "(BPA)",
  "(pan)": "(PAN)",
  "(ccl)": "(CCL)",
  "(sic)": "(SiC)",
};

const LOWER_CONNECTORS = new Set(["and", "or", "of", "in", "for", "to", "with"]);

function toCanonicalTitleCase(raw: string): string {
  return raw
    .split(/\s+/)
    .map((word, idx) => {
      const lower = word.toLowerCase();
      if (MIXED_CASE_TOKENS[lower]) return MIXED_CASE_TOKENS[lower];
      if (PRESERVE_UPPER_TOKENS.has(lower)) return word.toUpperCase();
      if (idx > 0 && LOWER_CONNECTORS.has(lower)) return lower;
      if (word.includes("-")) {
        return word
          .split("-")
          .map((part) => {
            const pLow = part.toLowerCase();
            if (MIXED_CASE_TOKENS[pLow]) return MIXED_CASE_TOKENS[pLow];
            if (PRESERVE_UPPER_TOKENS.has(pLow)) return part.toUpperCase();
            return part.charAt(0).toUpperCase() + part.slice(1).toLowerCase();
          })
          .join("-");
      }
      return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
    })
    .join(" ");
}

function canonicalizeRawMaterial(
  rawName: string,
  registryCode?: string | null
): string | null {
  if (registryCode && CANONICAL_CODE_NAMES[registryCode]) {
    return CANONICAL_CODE_NAMES[registryCode];
  }

  let cleaned = (rawName || "").trim();
  if (!cleaned) return null;

  // Reject compound overhead/logistics/generic phrases BEFORE stripping substrings
  if (INVALID_RAW_TOKENS_RE.test(cleaned)) {
    return null;
  }

  cleaned = cleaned.replace(/\s*\([^)]*\)/g, " ");
  cleaned = cleaned.replace(
    /\b(futures?|spot|benchmarks?|ind(ex|ices)|prices?|pricing|contracts?|quotes?)\b/gi,
    " "
  );
  cleaned = cleaned.replace(
    /^(european|global|us|u\.s\.|north\s+american|asian|chinese|domestic|imported|refined|raw|crude|primary|virgin|pure|ultra[- ]?pure|high[- ]?purity|electronic[- ]?grade|battery[- ]?grade|semiconductor[- ]?grade|industrial[- ]?grade|technical[- ]?grade|n[- ]?type)\s+/i,
    ""
  );

  cleaned = cleaned
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^[-–—,:;]+|[-–—,:;]+$/g, "");

  // Reject empty, too-short, or dangling conjunction strings ("And components", "Plastic resins and")
  if (
    !cleaned ||
    cleaned.length < 3 ||
    DANGLING_CONJUNCTION_RE.test(cleaned) ||
    INVALID_RAW_TOKENS_RE.test(cleaned)
  ) {
    return null;
  }

  const lookupKey = cleaned.toLowerCase().replace(/[^a-z0-9&]+/g, " ").trim();
  if (RAW_MATERIAL_ALIASES[lookupKey]) {
    return RAW_MATERIAL_ALIASES[lookupKey];
  }

  return toCanonicalTitleCase(cleaned);
}

function canonicalizeAdvancedMaterial(rawName: string): string {
  let expanded = (rawName || "").trim();
  expanded = expanded.replace(/\s*\([^)]*\)/g, " ");
  expanded = expanded
    .replace(/\bsemicon\b/gi, "Semiconductor")
    .replace(/\bchem\b/gi, "Chemicals")
    .replace(/\bmat\.?\b/gi, "Materials")
    .replace(/\bhigh[- ]perf\b/gi, "High-Performance")
    .replace(/\btech\b/gi, "Technologies")
    .replace(/\s+/g, " ")
    .trim();

  const lookupKey = expanded
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
  if (ADV_MATERIAL_ALIASES[lookupKey]) {
    return ADV_MATERIAL_ALIASES[lookupKey];
  }

  // Re-attach preserved parenthetical acronym if originally present
  const originalAcronym = (rawName || "").match(/\((CCL|SiC|SOI|ABF|HBM|OLED|EV)\)/i);
  const formatted = toCanonicalTitleCase(expanded);
  if (originalAcronym && !formatted.includes(originalAcronym[0].toUpperCase())) {
    return `${formatted} (${originalAcronym[1].toUpperCase()})`;
  }
  return formatted;
}

const CURATED_SUMMARIES: Record<string, string> = {
  "HG=F":
    "High-conductivity refined copper used for ultra-thin electrodeposited battery foil (4–6 µm), copper-clad PCB laminates, and high-voltage power interconnects.",
  "NG=F":
    "Primary hydrocarbon feedstock and process energy input for industrial hydrogen/syngas synthesis, ammonia/chemicals, and high-temperature carbon fiber carbonization ovens.",
  "CL=F":
    "Benchmark crude oil driving naphtha cracker economics and global polyethylene/polypropylene cost curves relative to US ethane-based producers.",
  "BZ=F":
    "International crude benchmark governing European and Asian naphtha feedstock costs across integrated specialty chemical and polymer value chains.",
  "ALI=F":
    "Lightweight structural metal alloyed with lithium, copper, and magnesium for aerospace fuselage plate, wing skins, and EV battery enclosures.",
  "PL=F":
    "Precious platinum-group metal essential for proton-exchange membrane (PEM) electrolyzers, automotive emission catalysts, and high-temperature specialty glass drawing bushings.",
  "SI=F":
    "Highest-conductivity metal used in conductive metallization pastes for semiconductor die-attach, chip packaging, and photovoltaic cells.",
  PNICKUSDM:
    "Class-1 high-purity nickel refined into nickel sulfate for high-energy NMC/NCMA EV cathodes and vacuum-melted into aerospace jet-engine superalloys.",
  PCOBAUSDM:
    "Strategic transition metal stabilizing layered oxide EV cathodes (NMC/NCA) and providing high-temperature creep resistance in aerospace turbine superalloys.",
  PNGASEUUSDM:
    "Natural gas feedstock and thermal energy input governing hydrogen/syngas synthesis, ammonia, and chemical plant conversion margins.",
  WPU101:
    "Benchmark ferrous and alloy steel input for clean specialty bar, seamless mechanical tubing, and bearing-grade aerospace/industrial steels.",
  NDPR_CN:
    "Neodymium-praseodymium rare earth oxide refined into NdFeB permanent magnets for EV traction motors, wind turbines, and defense precision actuators.",
  LICARB_CN:
    "Battery-grade lithium carbonate serving as the primary lithium source for EV cathodes and LiPF6 electrolyte salts.",
  GALLIUM_9999:
    "High-purity refined gallium (4N–7N) required to synthesize gallium arsenide (GaAs) and gallium nitride (GaN) compound semiconductor wafers for RF and power electronics.",
  GERMANIUM_INGOT:
    "Zone-refined germanium ingots used as lattice-matched substrates for multi-junction space solar cells and as core dopants in optical fiber preforms.",
  POLYSILICON_N:
    "Ultra-pure electronic-grade polysilicon (9N–11N purity) melted via the Czochralski process into monocrystalline 200mm and 300mm semiconductor silicon wafers.",
  SPHERICAL_GRAPHITE:
    "Purified and spheroidized natural flake or synthetic needle-coke graphite engineered into lithium-ion battery anodes for fast-charging EV cells.",
  COBALT_SULFATE:
    "Battery-grade cobalt sulfate precursor co-precipitated with nickel and manganese into high-nickel cathode active materials.",
  CARBON_FIBER_T700:
    "Polyacrylonitrile (PAN) precursor tow carbonized into high-tensile-strength fibers for aerospace structural prepregs, pressure vessels, and wind blades.",
};

function slugify(str: string): string {
  return str
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function normalizeMaterialKey(name: string): string {
  return name
    .toLowerCase()
    .replace(/\s*\([^)]*\)/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

export function buildMaterialsAtlas({
  equities,
  products,
  inputs,
  commodityDrivers,
  sensitivities,
  macroCodeSet,
}: {
  equities: MinimalEquity[];
  products: CompanyProduct[];
  inputs: MinimalInput[];
  commodityDrivers: MinimalDriver[];
  sensitivities: MinimalSensitivity[];
  macroCodeSet: Set<string>;
  inputNameMap?: Record<string, string>;
}): {
  rawMaterials: MaterialAtlasEntry[];
  advancedMaterials: MaterialAtlasEntry[];
} {
  const equityMap = new Map<string, MinimalEquity>();
  for (const eq of equities) {
    equityMap.set(eq.ticker, eq);
  }

  const inputInfoMap = new Map<string, MinimalInput>();
  for (const inp of inputs) {
    inputInfoMap.set(inp.input_code, inp);
  }

  // =========================================================================
  // 1. ALL RAW MATERIALS
  // =========================================================================
  interface RawBucket {
    name: string;
    categoryLabel: string;
    registryCode: string | null;
    endMarkets: Set<string>;
    mechanismNotes: string[];
    companies: Map<string, AtlasCompanyLink>;
  }

  const rawBuckets = new Map<string, RawBucket>();

  const getOrCreateRawBucket = (
    rawName: string,
    registryCode: string | null,
    categoryLabel: string
  ): RawBucket | null => {
    const canonicalName = canonicalizeRawMaterial(rawName, registryCode);
    if (!canonicalName) return null;

    const key = `raw:${normalizeMaterialKey(canonicalName)}`;
    let bucket = rawBuckets.get(key);
    if (!bucket) {
      bucket = {
        name: canonicalName,
        categoryLabel,
        registryCode,
        endMarkets: new Set<string>(),
        mechanismNotes: [],
        companies: new Map<string, AtlasCompanyLink>(),
      };
      rawBuckets.set(key, bucket);
    } else if (!bucket.registryCode && registryCode) {
      bucket.registryCode = registryCode;
    }
    return bucket;
  };

  const attachRawCompany = (
    bucket: RawBucket,
    ticker: string,
    origin: AtlasCompanyLink["sourceOrigin"],
    prodInfo?: {
      id?: string | number;
      name: string;
      category?: string;
      businessUnit?: string;
      detail?: string;
      endDemand?: string;
    }
  ) => {
    const eq = equityMap.get(ticker);
    if (!eq) return;
    let link = bucket.companies.get(ticker);
    if (!link) {
      link = {
        ticker: eq.ticker,
        companyName: eq.company_name,
        subSector: eq.sub_sector,
        country: eq.country,
        marketCapUsd: eq.market_cap_usd,
        contractType: eq.contract_type,
        linkedProducts: [],
        sourceOrigin: origin,
      };
      bucket.companies.set(ticker, link);
    }
    if (prodInfo) {
      const exists = link.linkedProducts.some(
        (p) => p.name.toLowerCase() === prodInfo.name.toLowerCase()
      );
      if (!exists) {
        link.linkedProducts.push(prodInfo);
      }
      if (prodInfo.endDemand) {
        bucket.endMarkets.add(prodInfo.endDemand);
      }
    }
  };

  for (const prod of products) {
    if (prod.status !== "active") continue;
    for (const rm of prod.raw_materials || []) {
      if (!rm.name) continue;
      const code = rm.commodity_ref || null;
      const inp = code ? inputInfoMap.get(code) : undefined;
      const bucket = getOrCreateRawBucket(
        rm.name,
        code,
        inp?.category || "Raw Material Feedstock"
      );
      if (!bucket) continue;
      attachRawCompany(bucket, prod.ticker, "product", {
        id: prod.id,
        name: prod.name,
        category: prod.category,
        businessUnit: prod.business_unit,
        detail: `${rm.role} (${rm.basis})`,
        endDemand: prod.downstream?.end_demand,
      });
    }
  }

  for (const drv of commodityDrivers) {
    const code = drv.matched_input_code || null;
    if (code && macroCodeSet.has(code)) continue;
    if (
      drv.exposure_role !== "Cost Input" &&
      (!code || !inputInfoMap.has(code))
    ) {
      continue;
    }
    const inp = code ? inputInfoMap.get(code) : undefined;
    const bucket = getOrCreateRawBucket(
      drv.commodity_name,
      code,
      inp?.category || "Raw Material Feedstock"
    );
    if (!bucket) continue;
    if (drv.mechanism_detail && bucket.mechanismNotes.length < 3) {
      bucket.mechanismNotes.push(drv.mechanism_detail);
    }
    attachRawCompany(bucket, drv.ticker, "filing_driver", {
      name: `${bucket.name} (${drv.exposure_role})`,
      detail: drv.impact_direction,
    });
  }

  for (const inp of inputs) {
    if (macroCodeSet.has(inp.input_code)) continue;
    if (inp.input_code === "WPU061" || inp.input_code === "WPU066") continue;

    const bucket = getOrCreateRawBucket(
      inp.input_name,
      inp.input_code,
      inp.category || "Tracked Commodity"
    );
    if (!bucket) continue;
    const matchingRules = sensitivities.filter(
      (s) =>
        s.input_code === inp.input_code && s.relationship_type === "Cost Input"
    );
    for (const rule of matchingRules) {
      if (rule.mechanism_note && bucket.mechanismNotes.length < 3) {
        bucket.mechanismNotes.push(rule.mechanism_note);
      }
      for (const eq of equities) {
        if (eq.sub_sector === rule.sub_sector && !bucket.companies.has(eq.ticker)) {
          attachRawCompany(bucket, eq.ticker, "sector_rule", {
            name: eq.sub_sector || "Core Segment",
            detail: rule.impact_direction,
          });
        }
      }
    }
  }

  const rawMaterials: MaterialAtlasEntry[] = Array.from(rawBuckets.values())
    .filter((b) => b.companies.size > 0)
    .map((b) => {
      const inp = b.registryCode ? inputInfoMap.get(b.registryCode) : undefined;
      const spotPriceLabel = inp
        ? `${Number(inp.value).toLocaleString(undefined, {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
          })} ${inp.unit || ""}`.trim()
        : null;

      const sortedCompanies = Array.from(b.companies.values()).sort(
        (a, c) => (c.marketCapUsd ?? -1) - (a.marketCapUsd ?? -1)
      );
      const endMarketsArr = Array.from(b.endMarkets).slice(0, 5);

      const summary =
        (b.registryCode && CURATED_SUMMARIES[b.registryCode]) ||
        b.mechanismNotes[0] ||
        `Physical raw material feedstock consumed across ${
          sortedCompanies.length
        } tracked advanced materials producer${
          sortedCompanies.length === 1 ? "" : "s"
        }${
          endMarketsArr.length > 0
            ? ` serving ${endMarketsArr.slice(0, 3).join(", ")}`
            : ""
        }.`;

      return {
        slug: `raw-${slugify(b.name)}`,
        kind: "raw" as const,
        name: b.name,
        categoryLabel: b.categoryLabel,
        registryCode: b.registryCode,
        spotPriceLabel,
        summary,
        endMarkets: endMarketsArr,
        companies: sortedCompanies,
      };
    })
    .sort(
      (a, b) =>
        b.companies.length - a.companies.length || a.name.localeCompare(b.name)
    );

  // =========================================================================
  // 2. ALL ADVANCED MATERIALS (Normalized & Abbreviation-Expanded)
  // =========================================================================
  interface AdvBucket {
    name: string;
    categoryLabel: string;
    specs: string[];
    endMarkets: Set<string>;
    companies: Map<string, AtlasCompanyLink>;
  }

  const advBuckets = new Map<string, AdvBucket>();

  const getOrCreateAdvBucket = (
    matName: string,
    categoryLabel: string
  ): AdvBucket => {
    const formattedName = canonicalizeAdvancedMaterial(matName);
    const key = normalizeMaterialKey(formattedName) || formattedName.toLowerCase();
    let bucket = advBuckets.get(key);
    if (!bucket) {
      bucket = {
        name: formattedName,
        categoryLabel,
        specs: [],
        endMarkets: new Set<string>(),
        companies: new Map<string, AtlasCompanyLink>(),
      };
      advBuckets.set(key, bucket);
    } else if (
      bucket.categoryLabel === "Universe Vertical" &&
      categoryLabel === "Advanced Material Class"
    ) {
      bucket.categoryLabel = "Advanced Material Class";
    }
    return bucket;
  };

  for (const prod of products) {
    if (prod.status !== "active") continue;
    const rawCat = (prod.category || prod.name || "").trim();
    const catName =
      GENERIC_ADV_BUCKET_RE.test(rawCat) &&
      prod.name &&
      !GENERIC_ADV_BUCKET_RE.test(prod.name)
        ? prod.name
        : rawCat;
    if (!catName || GENERIC_ADV_BUCKET_RE.test(catName)) continue;

    const bucket = getOrCreateAdvBucket(catName, "Advanced Material Class");
    if (prod.advanced?.performance_spec && bucket.specs.length < 3) {
      bucket.specs.push(prod.advanced.performance_spec);
    }
    if (prod.downstream?.end_demand) {
      bucket.endMarkets.add(prod.downstream.end_demand);
    }

    const eq = equityMap.get(prod.ticker);
    if (!eq) continue;
    let link = bucket.companies.get(prod.ticker);
    if (!link) {
      link = {
        ticker: eq.ticker,
        companyName: eq.company_name,
        subSector: eq.sub_sector,
        country: eq.country,
        marketCapUsd: eq.market_cap_usd,
        contractType: eq.contract_type,
        linkedProducts: [],
        sourceOrigin: "product",
      };
      bucket.companies.set(prod.ticker, link);
    }
    link.linkedProducts.push({
      id: prod.id,
      name: prod.name,
      category: prod.category,
      businessUnit: prod.business_unit,
      detail: prod.advanced?.performance_spec,
      endDemand: prod.downstream?.end_demand,
    });
  }

  for (const eq of equities) {
    if (!eq.sub_sector || GENERIC_ADV_BUCKET_RE.test(eq.sub_sector)) continue;
    const bucket = getOrCreateAdvBucket(eq.sub_sector, "Universe Vertical");
    if (!bucket.companies.has(eq.ticker)) {
      bucket.companies.set(eq.ticker, {
        ticker: eq.ticker,
        companyName: eq.company_name,
        subSector: eq.sub_sector,
        country: eq.country,
        marketCapUsd: eq.market_cap_usd,
        contractType: eq.contract_type,
        linkedProducts: [
          {
            name: eq.sub_sector,
            detail: "Primary advanced materials segment",
          },
        ],
        sourceOrigin: "sector_rule",
      });
    }
  }

  const advancedMaterials: MaterialAtlasEntry[] = Array.from(
    advBuckets.values()
  )
    .filter((b) => b.companies.size > 0)
    .map((b) => {
      const sortedCompanies = Array.from(b.companies.values()).sort(
        (a, c) => (c.marketCapUsd ?? -1) - (a.marketCapUsd ?? -1)
      );
      const endMarketsArr = Array.from(b.endMarkets).slice(0, 5);
      const summary =
        b.specs[0] ||
        `Engineered advanced material manufactured and supplied by ${
          sortedCompanies.length
        } tracked compan${sortedCompanies.length === 1 ? "y" : "ies"}${
          endMarketsArr.length > 0
            ? ` for ${endMarketsArr.slice(0, 3).join(", ")} applications`
            : ""
        }.`;

      return {
        slug: `adv-${slugify(b.name)}`,
        kind: "advanced" as const,
        name: b.name,
        categoryLabel: b.categoryLabel,
        registryCode: null,
        spotPriceLabel: null,
        summary,
        endMarkets: endMarketsArr,
        companies: sortedCompanies,
      };
    })
    .sort(
      (a, b) =>
        b.companies.length - a.companies.length || a.name.localeCompare(b.name)
    );

  return { rawMaterials, advancedMaterials };
}
