import { CompanyProduct } from "./productRollup";

export type MaterialTier = "T0" | "T1" | "T2" | "T3";
export type MaterialLinkRole = "sells" | "consumes" | "makes_captive";

export interface MaterialRecord {
  id: number;
  slug: string;
  canonical_name: string;
  tier: MaterialTier;
  material_group: string;
  registry_code: string | null;
  summary: string | null;
}

export interface CompanyMaterialLinkRow {
  ticker: string;
  material_id: number;
  role: MaterialLinkRole;
  product_name: string | null;
  business_unit: string | null;
  performance_spec: string | null;
  end_demand: string | null;
  source_section: string | null;
  source_quote: string;
  confidence: string | null;
}

export interface AtlasCompanyLink {
  ticker: string;
  companyName: string;
  subSector: string | null;
  country: string | null;
  marketCapUsd: number | null;
  contractType: string | null;
  role: MaterialLinkRole;
  linkedProducts: {
    id?: string | number;
    name: string;
    category?: string;
    businessUnit?: string;
    detail?: string;
    endDemand?: string;
    sourceQuote?: string;
  }[];
  sourceOrigin: "material_link" | "product" | "filing_driver" | "sector_rule";
}

export interface MaterialAtlasEntry {
  slug: string;
  kind: "raw" | "advanced";
  tier: MaterialTier;
  name: string;
  categoryLabel: string;
  registryCode: string | null;
  spotPriceLabel: string | null;
  summary: string;
  endMarkets: string[];
  companies: AtlasCompanyLink[];
  producers: AtlasCompanyLink[];
  consumers: AtlasCompanyLink[];
  captiveUsers: AtlasCompanyLink[];
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

const VAGUE_OR_NON_MATERIAL_ADV_RE =
  /^(electronic|electronic\s+materials?|advanced\s+materials?(\s+portfolio)?|specialty\s+materials?|performance\s+chemicals?|engineered\s+materials?|industrial\s+materials?|semiconductor\s+materials?|battery\s+materials?|materials?|chemicals?|polymers?|resins?|plastics?|metals?|alloys?|mining|rare\s+earth(\s+mining)?|specialty\s+chemicals?|industrial\s+chemicals?|composite\s+materials?|advanced\s+polymers?|electronic\s+chemicals?|batteries|battery\s+cells?|devices?|parts?|components?|aerospace|aerospace\s+and\s+defense|fuel|fuels|life\s+science|life\s+sciences|mobility|optics|nuclear|nuc\)?)$/i;

const COHESIVE_AND_CLASSES = new Set([
  "cmp slurries and pads",
  "specialty steel and alloys",
  "stainless and high performance alloys",
  "industrial and specialty gases",
  "polyurethanes and isocyanates",
  "polyolefins and engineered resins",
  "rare earth oxides and metals",
  "lithium hydroxide and carbonate",
  "lithium carbonate and hydroxide",
  "fluoropolymers and fluorochemicals",
  "compound semiconductors and photonics",
  "photoresists and semiconductor chemicals",
  "biocompatible polymers and biomaterials",
  "optical fiber and display glass",
  "polished and epitaxial silicon wafers",
  "sic substrates and epi",
  "gaas and inp wafers",
  "spin on dielectrics and low k materials",
  "ald and cvd precursors",
  "underfill and die attach",
  "fused quartz and fused silica",
  "optical fiber and preforms",
  "electrolytes and salts",
  "carbon fiber and prepreg",
  "structural adhesives and formulated epoxies",
  "ndfeb and smco magnets",
  "refining and emissions catalysts",
]);

const CANONICAL_CODE_NAMES: Record<string, string> = {
  "HG=F": "Copper",
  "NG=F": "Natural Gas",
  "CL=F": "Crude Oil",
  "BZ=F": "Crude Oil",
  "ALI=F": "Aluminum",
  "PL=F": "Platinum",
  "SI=F": "Silver",
  "PA=F": "Palladium",
  "GC=F": "Gold",
  PNICKUSDM: "Nickel",
  PCOBAUSDM: "Cobalt",
  PNGASEUUSDM: "Natural Gas",
  WPU101: "Iron Ore",
  NDPR_CN: "NdPr, Dysprosium and Terbium Oxides",
  LICARB_CN: "Lithium Carbonate and Hydroxide",
  GALLIUM_9999: "Gallium",
  GERMANIUM_INGOT: "Germanium",
  POLYSILICON_N: "Polysilicon",
  SPHERICAL_GRAPHITE: "Spherical Graphite",
  COBALT_SULFATE: "Nickel and Cobalt Sulfate",
  CARBON_FIBER_T700: "PAN Precursor",
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
  "crude oil": "Crude Oil",
  "wti crude": "Crude Oil",
  "wti crude oil": "Crude Oil",
  "brent crude": "Crude Oil",
  "brent crude oil": "Crude Oil",
  aluminum: "Aluminum",
  aluminium: "Aluminum",
  "primary aluminum": "Aluminum",
  nickel: "Nickel",
  "class 1 nickel": "Nickel",
  "nickel sulfate": "Nickel and Cobalt Sulfate",
  "nickel sulphate": "Nickel and Cobalt Sulfate",
  cobalt: "Cobalt",
  "cobalt sulfate": "Nickel and Cobalt Sulfate",
  "cobalt sulphate": "Nickel and Cobalt Sulfate",
  "nickel and cobalt sulfate": "Nickel and Cobalt Sulfate",
  lithium: "Lithium Spodumene and Brine",
  "lithium carbonate": "Lithium Carbonate and Hydroxide",
  "lithium hydroxide": "Lithium Carbonate and Hydroxide",
  "lithium carbonate and hydroxide": "Lithium Carbonate and Hydroxide",
  "lithium hydroxide and carbonate": "Lithium Carbonate and Hydroxide",
  spodumene: "Lithium Spodumene and Brine",
  "spodumene concentrate": "Lithium Spodumene and Brine",
  graphite: "Flake Graphite",
  "natural graphite": "Flake Graphite",
  "natural flake graphite": "Flake Graphite",
  "flake graphite": "Flake Graphite",
  "spherical graphite": "Spherical Graphite",
  "needle coke": "Needle and Calcined Coke",
  "petroleum needle coke": "Needle and Calcined Coke",
  "calcined petroleum coke": "Needle and Calcined Coke",
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
  molybdenum: "Molybdenum",
  titanium: "Titanium Sponge and Ore",
  "titanium sponge": "Titanium Sponge and Ore",
  ilmenite: "Titanium Sponge and Ore",
  rutile: "Titanium Sponge and Ore",
  "titanium tetrachloride": "Titanium Tetrachloride",
  beryllium: "Beryllium",
  zirconium: "Zirconium",
  hafnium: "Hafnium",
  platinum: "Platinum",
  palladium: "Palladium",
  rhodium: "Rhodium",
  iridium: "Iridium",
  ruthenium: "Ruthenium",
  silver: "Silver",
  gold: "Gold",
  ndpr: "NdPr, Dysprosium and Terbium Oxides",
  "ndpr oxide": "NdPr, Dysprosium and Terbium Oxides",
  "neodymium praseodymium": "NdPr, Dysprosium and Terbium Oxides",
  "neodymium praseodymium oxide": "NdPr, Dysprosium and Terbium Oxides",
  "dysprosium oxide": "NdPr, Dysprosium and Terbium Oxides",
  "terbium oxide": "NdPr, Dysprosium and Terbium Oxides",
  "rare earth concentrate": "Rare Earth Concentrate",
  bastnaesite: "Rare Earth Concentrate",
  monazite: "Rare Earth Concentrate",
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
  chlorine: "Chlorine",
  "caustic soda": "Caustic Soda",
  "sodium hydroxide": "Caustic Soda",
  "hydrofluoric acid": "Hydrofluoric Acid",
  "hydrogen fluoride": "Hydrofluoric Acid",
  fluorspar: "Fluorspar",
  "sulfuric acid": "Sulfuric Acid",
  "phosphoric acid": "Phosphoric Acid",
  "nitric acid": "Nitric Acid",
  "hydrochloric acid": "Hydrochloric Acid",
  "quartz sand": "Quartz and Silica Sand",
  "silica sand": "Quartz and Silica Sand",
  bauxite: "Bauxite",
  magnesite: "Magnesite",
  boron: "Boron Minerals",
  colemanite: "Boron Minerals",
  bisphenol: "Bisphenol A",
  "bisphenol a": "Bisphenol A",
  epichlorohydrin: "Epichlorohydrin",
  acrylonitrile: "Acrylonitrile",
  polyacrylonitrile: "PAN Precursor",
  "pan precursor": "PAN Precursor",
  "wood pulp": "Dissolving Pulp",
  "dissolving wood pulp": "Dissolving Pulp",
  "dissolving pulp": "Dissolving Pulp",
  "iron ore": "Iron Ore",
  "steel scrap": "Steel Scrap",
};

// Intrinsic tier registry for fallback sources (when company_material_links has not populated a ticker yet)
const INTRINSIC_TIER_MAP: Record<string, MaterialTier> = {
  // T1 Intermediates (must never become T2 advanced products even when sold)
  polysilicon: "T1",
  "metallurgical silicon": "T1",
  "lithium carbonate and hydroxide": "T1",
  "lithium hydroxide and carbonate": "T1",
  "lithium carbonate": "T1",
  "lithium hydroxide": "T1",
  "nickel and cobalt sulfate": "T1",
  "nickel sulfate": "T1",
  "cobalt sulfate": "T1",
  "ndpr dysprosium and terbium oxides": "T1",
  "rare earth oxides": "T1",
  "rare earth oxides and metals": "T1",
  "pan precursor": "T1",
  "spherical graphite": "T1",
  "high purity alumina": "T1",
  "titanium tetrachloride": "T1",
  "germanium tetrachloride": "T1",
  "mdi and tdi": "T1",
  "dissolving pulp": "T1",
  // T0 Primary commodities
  copper: "T0",
  aluminum: "T0",
  nickel: "T0",
  cobalt: "T0",
  manganese: "T0",
  zinc: "T0",
  tin: "T0",
  "iron ore": "T0",
  ferroalloys: "T0",
  "steel scrap": "T0",
  platinum: "T0",
  palladium: "T0",
  rhodium: "T0",
  iridium: "T0",
  ruthenium: "T0",
  silver: "T0",
  gold: "T0",
  "lithium spodumene and brine": "T0",
  gallium: "T0",
  germanium: "T0",
  indium: "T0",
  tantalum: "T0",
  niobium: "T0",
  tungsten: "T0",
  molybdenum: "T0",
  vanadium: "T0",
  "titanium sponge and ore": "T0",
  zirconium: "T0",
  hafnium: "T0",
  beryllium: "T0",
  antimony: "T0",
  magnesium: "T0",
  "rare earth concentrate": "T0",
  "quartz and silica sand": "T0",
  bauxite: "T0",
  magnesite: "T0",
  fluorspar: "T0",
  "boron minerals": "T0",
  "flake graphite": "T0",
  "soda ash": "T0",
  "natural gas": "T0",
  "crude oil": "T0",
  ethane: "T0",
  naphtha: "T0",
  "needle and calcined coke": "T0",
  "coal tar pitch": "T0",
  ethylene: "T0",
  propylene: "T0",
  benzene: "T0",
  toluene: "T0",
  xylene: "T0",
  butadiene: "T0",
  styrene: "T0",
  phenol: "T0",
  acetone: "T0",
  methanol: "T0",
  ammonia: "T0",
  acrylonitrile: "T0",
  "bisphenol a": "T0",
  epichlorohydrin: "T0",
  chlorine: "T0",
  "caustic soda": "T0",
  "sulfuric acid": "T0",
  "hydrochloric acid": "T0",
  "nitric acid": "T0",
  "phosphoric acid": "T0",
  "hydrofluoric acid": "T0",
  "hydrogen peroxide": "T0",
};

const ADV_MATERIAL_ALIASES: Record<string, string> = {
  "battery electrolyte": "Electrolytes and Salts (LiPF6)",
  "battery electrolytes": "Electrolytes and Salts (LiPF6)",
  "electrolytes and salts": "Electrolytes and Salts (LiPF6)",
  fluorochem: "Fluoropolymers (PTFE, PFA, PVDF)",
  fluorochemicals: "Fluoropolymers (PTFE, PFA, PVDF)",
  fluoropolymers: "Fluoropolymers (PTFE, PFA, PVDF)",
  "fluoropolymers and titanium technologies": "Fluoropolymers (PTFE, PFA, PVDF)",
  "carbon fiber": "Carbon Fiber and Prepreg",
  "carbon fibers": "Carbon Fiber and Prepreg",
  "carbon fiber composites": "Carbon Fiber and Prepreg",
  "aerospace carbon fiber composites": "Carbon Fiber and Prepreg",
  "aramid fiber": "Aramid Fibers",
  "aramid fibers": "Aramid Fibers",
  "advanced films": "Polyimide Films",
  "polymer films": "Polyimide Films",
  "lithium refining": "Lithium Carbonate and Hydroxide",
  "lithium chemicals": "Lithium Carbonate and Hydroxide",
  "lithium carbonate": "Lithium Carbonate and Hydroxide",
  "lithium hydroxide": "Lithium Carbonate and Hydroxide",
  "rare earth mining and magnets": "NdFeB and SmCo Magnets",
  "rare earth magnets": "NdFeB and SmCo Magnets",
  "ndfeb permanent magnets": "NdFeB and SmCo Magnets",
  "rare earth permanent magnets": "NdFeB and SmCo Magnets",
  "rare earth oxides": "NdPr, Dysprosium and Terbium Oxides",
  "rare earth metals": "NdPr, Dysprosium and Terbium Oxides",
  "rare earth oxides and metals": "NdPr, Dysprosium and Terbium Oxides",
  "specialty steel": "Specialty Steel and Alloys",
  "specialty steel and alloys": "Specialty Steel and Alloys",
  "stainless and specialty steel": "Specialty Steel and Alloys",
  "stainless and high performance alloys": "Specialty Steel and Alloys",
  photoresists: "Photoresists (KrF, ArF, EUV)",
  "photoresists and semiconductor chemicals": "Photoresists (KrF, ArF, EUV)",
  "semiconductor packaging": "Epoxy Molding Compounds",
  "semiconductor packaging and chemicals": "Epoxy Molding Compounds",
  "compound semiconductors": "GaAs and InP Wafers",
  "compound semiconductors and photonics": "GaAs and InP Wafers",
  "natural graphite anode materials": "Anode Materials (Graphite, Silicon-Carbon)",
  "synthetic graphite anodes": "Anode Materials (Graphite, Silicon-Carbon)",
  "synthetic graphite anode materials": "Anode Materials (Graphite, Silicon-Carbon)",
  "copper clad laminates": "High-Speed Copper Clad Laminates",
  "high speed copper clad laminates": "High-Speed Copper Clad Laminates",
  "high frequency copper clad laminates": "High-Speed Copper Clad Laminates",
  "silicon wafer": "Polished and Epitaxial Silicon Wafers",
  "silicon wafers": "Polished and Epitaxial Silicon Wafers",
  "silicon wafers and pvc": "Polished and Epitaxial Silicon Wafers",
  "engineered soi wafers": "SOI Wafers",
  "silicon carbide wafer": "SiC Substrates and Epi",
  "silicon carbide wafers": "SiC Substrates and Epi",
  "sic wafers": "SiC Substrates and Epi",
  "cmp slurries": "CMP Slurries",
  "cmp slurries and pads": "CMP Slurries",
  "cmp slurries and wet chemicals": "CMP Slurries",
  "cmp slurries and abrasives": "CMP Slurries",
  "high nickel cathodes": "Cathode Active Materials (NMC, NCA, LFP)",
  "high nickel cathode materials": "Cathode Active Materials (NMC, NCA, LFP)",
  "cathodes and synthetic anodes": "Cathode Active Materials (NMC, NCA, LFP)",
  "cathode active materials": "Cathode Active Materials (NMC, NCA, LFP)",
  "medical devices and biomaterials": "Medical-Grade Polymers",
  biomaterials: "Medical-Grade Polymers",
  ceram: "Advanced Ceramics",
  ceramic: "Advanced Ceramics",
  ceramics: "Advanced Ceramics",
  "hydrogen cat": "PGM Catalysts",
  "hydrogen catalysts": "PGM Catalysts",
};

const SPELLING_FIXES: [RegExp, string][] = [
  [/\bspeciality\b/gi, "Specialty"],
  [/\bspecialities\b/gi, "Specialties"],
];
const fixSpelling = (s: string) =>
  SPELLING_FIXES.reduce((acc, [re, rep]) => acc.replace(re, rep), s);
const stripParens = (s: string) => s.replace(/\s*\([^)]*\)?/g, " ");

const STOP_TOKENS = new Set([
  "and", "or", "of", "for", "in", "the", "to", "with", "a", "an", "from",
  "used", "serving", "supporting", "enabling",
]);

const MODIFIER_TOKENS = new Set([
  "stainless", "specialty", "specialties", "advanced", "engineered", "performance",
  "high", "industrial", "electronic", "precision", "structural", "functional",
  "premium", "technical", "specialized", "custom", "proprietary", "next",
  "generation", "novel", "innovative", "critical", "strategic", "synthetic",
  "natural", "ultra", "pure", "purity", "grade",
]);

const GENERIC_HEAD_TOKENS = new Set([
  "material", "materials", "chemical", "chemicals", "product", "products",
  "solution", "solutions", "technology", "technologies", "system", "systems",
  "service", "services", "portfolio", "offering", "offerings", "business",
  "segment", "segments", "division", "line", "equipment", "device", "devices",
  "component", "components", "part", "parts", "application", "applications",
  "market", "markets", "industry", "industries", "sector", "sectors",
  "mineral", "minerals", "ore", "ores", "metal", "metals",
]);

const PROCESS_TOKENS = new Set([
  "mining", "refining", "processing", "recycling", "smelting", "extraction",
  "production", "manufacturing", "fabrication", "separation", "distribution",
  "trading", "supply", "sales", "beneficiation",
]);

const END_MARKET_TOKENS = new Set([
  "semiconductor", "semiconductors", "electronics", "aerospace", "defense",
  "defence", "automotive", "medical", "healthcare", "energy", "nuclear",
  "mobility", "optics", "battery", "batteries",
]);

const BARE_AMBIGUOUS_PHRASES = new Set(["rare earth", "rare earths"]);

const DESCRIPTIVE_PHRASE_RE =
  /^(materials?|chemicals?|products?|solutions?|technolog(y|ies)|components?|equipment|systems?|services?|supplies|consumables?)\s+(for|used\s+(in|for)|in|serving|supporting|enabling|to)\b|\b(manufacturing|fabrication|industry|industries|sectors?|markets?|applications?|segments?|business|division|operations)$/i;

const RAW_KEY_SET = new Set<string>(
  [...Object.keys(RAW_MATERIAL_ALIASES), ...Object.values(RAW_MATERIAL_ALIASES)].map(
    normalizeMaterialKey
  )
);

const FAMILY_ALIASES: [RegExp, string][] = [
  [/\b(stainless|specialty)\b.*\b(steels?|alloys?)\b/, "Specialty Steel and Alloys"],
  [/\brare earth\b.*\b(refin\w*|process\w*|separat\w*)\b/, "NdPr, Dysprosium and Terbium Oxides"],
];

function matchFamilyAlias(lookupKey: string): string | null {
  for (const [re, canonical] of FAMILY_ALIASES) {
    if (re.test(lookupKey)) return canonical;
  }
  return null;
}

function tokenize(s: string): string[] {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim().split(" ").filter(Boolean);
}

function isInformativeProduct(phrase: string): boolean {
  const residual = tokenize(phrase).filter(
    (t) =>
      !STOP_TOKENS.has(t) &&
      !MODIFIER_TOKENS.has(t) &&
      !GENERIC_HEAD_TOKENS.has(t) &&
      !PROCESS_TOKENS.has(t) &&
      !END_MARKET_TOKENS.has(t)
  );
  if (residual.length === 0) return false;
  return !BARE_AMBIGUOUS_PHRASES.has(residual.join(" "));
}

function advRejectionReason(cleaned: string, lookupKey: string): string | null {
  if (VAGUE_OR_NON_MATERIAL_ADV_RE.test(cleaned)) return "vague-umbrella";
  if (DESCRIPTIVE_PHRASE_RE.test(cleaned)) return "descriptive-phrase";
  if (RAW_KEY_SET.has(lookupKey)) return "raw-feedstock";
  if (!isInformativeProduct(cleaned)) return "no-product-noun";
  return null;
}

const PRESERVE_UPPER_TOKENS = new Set([
  "wti", "pan", "bpa", "lng", "pvc", "ptfe", "pfa", "peek", "pps",
  "mdi", "tdi", "hcl", "hf", "h2so4", "lipf6", "sic", "gan", "gaas",
  "inp", "sio2", "al2o3", "tio2", "ticl4", "gecl4", "ccl", "abf", "cmp",
  "euv", "arf", "oled", "mlcc", "soi", "hbm", "ic", "rf", "ev", "pgm",
  "pvoh", "ncma", "nmc", "lfp", "ndfeb", "nf3", "wf6", "ald", "cvd", "pfsa",
]);

const MIXED_CASE_TOKENS: Record<string, string> = {
  ndpr: "NdPr",
  "(ndpr)": "(NdPr)",
  "(bpa)": "(BPA)",
  "(pan)": "(PAN)",
  "(ccl)": "(CCL)",
  "(sic)": "(SiC)",
  "(lipf6)": "(LiPF6)",
  "smco": "SmCo",
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

  if (INVALID_RAW_TOKENS_RE.test(cleaned)) {
    return null;
  }

  cleaned = stripParens(cleaned);
  cleaned = cleaned.replace(/\./g, "");
  cleaned = cleaned.replace(/\s*&\s*/g, " and ");
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

  if (
    !cleaned ||
    cleaned.length < 3 ||
    DANGLING_CONJUNCTION_RE.test(cleaned) ||
    INVALID_RAW_TOKENS_RE.test(cleaned)
  ) {
    return null;
  }

  const lookupKey = cleaned.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  if (RAW_MATERIAL_ALIASES[lookupKey]) {
    return RAW_MATERIAL_ALIASES[lookupKey];
  }

  return toCanonicalTitleCase(cleaned);
}

function normalizeSingleAdvSegment(
  segment: string,
  originalAcronym: RegExpMatchArray | null
): string | null {
  const cleaned = stripParens(fixSpelling(segment))
    .replace(/^[^(\s]*\)/g, " ")
    .replace(/\./g, "")
    .replace(/\bict\b/gi, "Information and Communications Technology")
    .replace(/\bsemicon\b/gi, "Semiconductor")
    .replace(/\bpkg\b/gi, "Packaging")
    .replace(/\bind\b/gi, "Industrial")
    .replace(/\bchem\b/gi, "Chemicals")
    .replace(/\bfluorochem\b/gi, "Fluorochemicals")
    .replace(/\bceram\b/gi, "Ceramics")
    .replace(/\bcat\b/gi, "Catalysts")
    .replace(/\bmat\b/gi, "Materials")
    .replace(/\bhigh[- ]perf\b/gi, "High-Performance")
    .replace(/\btech\b/gi, "Technologies")
    .replace(/\bmfg\b/gi, "Manufacturing")
    .replace(/\bmining\b/gi, "")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^[-–—,:;()]+|[-–—,:;()]+$/g, "");

  if (!cleaned || cleaned.length < 3 || DANGLING_CONJUNCTION_RE.test(cleaned)) {
    return null;
  }

  const lookupKey = cleaned
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();

  if (ADV_MATERIAL_ALIASES[lookupKey]) {
    return ADV_MATERIAL_ALIASES[lookupKey];
  }
  const fam = matchFamilyAlias(lookupKey);
  if (fam) {
    return fam;
  }

  if (advRejectionReason(cleaned, lookupKey)) {
    return null;
  }

  const formatted = toCanonicalTitleCase(cleaned);
  if (originalAcronym && !formatted.includes(originalAcronym[0].toUpperCase())) {
    return `${formatted} (${originalAcronym[1].toUpperCase()})`;
  }
  return formatted;
}

function resolveAdvancedMaterialClasses(rawName: string): string[] {
  const raw = fixSpelling((rawName || "").trim());
  if (!raw) return [];

  const originalAcronym = raw.match(/\((CCL|SiC|SOI|ABF|HBM|OLED|EV|PTFE)\)/i);

  const normalizedConnectors = raw
    .replace(/\./g, "")
    .replace(/\s*&\s*/g, " and ")
    .replace(/\s*\/\s*/g, " and ")
    .replace(/\s+/g, " ")
    .trim();

  const fullLookupKey = stripParens(normalizedConnectors)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();

  if (ADV_MATERIAL_ALIASES[fullLookupKey]) {
    return [ADV_MATERIAL_ALIASES[fullLookupKey]];
  }
  const fam = matchFamilyAlias(fullLookupKey);
  if (fam) {
    return [fam];
  }
  if (COHESIVE_AND_CLASSES.has(fullLookupKey)) {
    const single = normalizeSingleAdvSegment(normalizedConnectors, originalAcronym);
    return single ? [single] : [];
  }

  if (/\band\b/i.test(normalizedConnectors)) {
    const parts = normalizedConnectors.split(/\s+\band\b\s+/i);
    const hasFragment = parts.some((p) => !isInformativeProduct(stripParens(p)));
    if (!hasFragment) {
      const results: string[] = [];
      const seen = new Set<string>();
      for (const part of parts) {
        const resolved = normalizeSingleAdvSegment(part, originalAcronym);
        if (resolved && !seen.has(resolved.toLowerCase())) {
          seen.add(resolved.toLowerCase());
          results.push(resolved);
        }
      }
      return results;
    }
  }

  const single = normalizeSingleAdvSegment(normalizedConnectors, originalAcronym);
  return single ? [single] : [];
}

function resolveIntrinsicTier(canonicalName: string, defaultTier: MaterialTier): MaterialTier {
  const k = normalizeMaterialKey(canonicalName);
  return INTRINSIC_TIER_MAP[k] || defaultTier;
}

const CURATED_SUMMARIES: Record<string, string> = {
  "HG=F":
    "High-conductivity refined copper used for ultra-thin electrodeposited battery foil (4–6 µm), copper-clad PCB laminates, and high-voltage power interconnects.",
  "NG=F":
    "Hydrocarbon feedstock for steam methane reforming (hydrogen/syngas), ammonia synthesis, and high-temperature carbonization furnaces.",
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
    "Benchmark ferrous feedstock for clean specialty bar, seamless mechanical tubing, and bearing-grade aerospace/industrial steels.",
  NDPR_CN:
    "Separated neodymium, praseodymium, dysprosium, and terbium oxides refined into NdFeB permanent magnets for EV traction motors and defense actuators.",
  LICARB_CN:
    "Battery-grade lithium carbonate and lithium hydroxide monohydrate traded as primary lithium chemical salts for EV cathodes and electrolytes.",
  GALLIUM_9999:
    "High-purity refined gallium (4N–7N) required to synthesize gallium arsenide (GaAs) and gallium nitride (GaN) compound semiconductor wafers for RF and power electronics.",
  GERMANIUM_INGOT:
    "Zone-refined germanium ingots used as lattice-matched substrates for multi-junction space solar cells and as core dopants in optical fiber preforms.",
  POLYSILICON_N:
    "Ultra-pure electronic-grade polysilicon (9N–11N purity) melted via the Czochralski process into monocrystalline 200mm and 300mm semiconductor silicon wafers.",
  SPHERICAL_GRAPHITE:
    "Purified and spheroidized natural flake graphite engineered into lithium-ion battery anodes for fast-charging EV cells.",
  COBALT_SULFATE:
    "Battery-grade nickel and cobalt sulfate crystals co-precipitated into high-nickel cathode active material precursors.",
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
    .replace(/\s*&\s*/g, " and ")
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
  materials = [],
  materialLinks = [],
}: {
  equities: MinimalEquity[];
  products: CompanyProduct[];
  inputs: MinimalInput[];
  commodityDrivers: MinimalDriver[];
  sensitivities: MinimalSensitivity[];
  macroCodeSet: Set<string>;
  inputNameMap?: Record<string, string>;
  materials?: MaterialRecord[];
  materialLinks?: CompanyMaterialLinkRow[];
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

  // Unified material registry keyed by normalized name (tier belongs to material, role belongs to link)
  interface UnifiedBucket {
    slug: string;
    name: string;
    tier: MaterialTier;
    categoryLabel: string;
    registryCode: string | null;
    summaryOverride: string | null;
    specs: string[];
    endMarkets: Set<string>;
    mechanismNotes: string[];
    producers: Map<string, AtlasCompanyLink>;
    consumers: Map<string, AtlasCompanyLink>;
    captiveUsers: Map<string, AtlasCompanyLink>;
  }

  const unifiedBuckets = new Map<string, UnifiedBucket>();
  const materialById = new Map<number, MaterialRecord>();
  for (const m of materials) {
    materialById.set(m.id, m);
  }

  const getOrCreateUnifiedBucket = (
    name: string,
    tier: MaterialTier,
    categoryLabel: string,
    opts?: {
      slug?: string;
      registryCode?: string | null;
      summary?: string | null;
    }
  ): UnifiedBucket | null => {
    if (tier === "T3") return null;
    const key = normalizeMaterialKey(name);
    if (!key) return null;

    let bucket = unifiedBuckets.get(key);
    if (!bucket) {
      bucket = {
        slug:
          opts?.slug ||
          `${tier === "T2" ? "adv" : "raw"}-${slugify(name)}`,
        name,
        tier,
        categoryLabel,
        registryCode: opts?.registryCode || null,
        summaryOverride: opts?.summary || null,
        specs: [],
        endMarkets: new Set<string>(),
        mechanismNotes: [],
        producers: new Map<string, AtlasCompanyLink>(),
        consumers: new Map<string, AtlasCompanyLink>(),
        captiveUsers: new Map<string, AtlasCompanyLink>(),
      };
      unifiedBuckets.set(key, bucket);
    } else {
      if (!bucket.registryCode && opts?.registryCode) {
        bucket.registryCode = opts.registryCode;
      }
      if (!bucket.summaryOverride && opts?.summary) {
        bucket.summaryOverride = opts.summary;
      }
      if (
        bucket.categoryLabel === "Universe Vertical" &&
        categoryLabel !== "Universe Vertical"
      ) {
        bucket.categoryLabel = categoryLabel;
      }
    }
    return bucket;
  };

  const attachCompanyRole = (
    bucket: UnifiedBucket,
    ticker: string,
    role: MaterialLinkRole,
    origin: AtlasCompanyLink["sourceOrigin"],
    prodInfo?: {
      id?: string | number;
      name: string;
      category?: string;
      businessUnit?: string;
      detail?: string;
      endDemand?: string;
      sourceQuote?: string;
    }
  ) => {
    const eq = equityMap.get(ticker);
    if (!eq) return;

    const targetMap =
      role === "sells"
        ? bucket.producers
        : role === "consumes"
        ? bucket.consumers
        : bucket.captiveUsers;

    let link = targetMap.get(ticker);
    if (!link) {
      link = {
        ticker: eq.ticker,
        companyName: eq.company_name,
        subSector: eq.sub_sector,
        country: eq.country,
        marketCapUsd: eq.market_cap_usd,
        contractType: eq.contract_type,
        role,
        linkedProducts: [],
        sourceOrigin: origin,
      };
      targetMap.set(ticker, link);
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

  // =========================================================================
  // 1. PRIMARY SOURCE: Relational `materials` + `company_material_links`
  // =========================================================================
  const linkedTickers = new Set<string>();
  for (const link of materialLinks) {
    const mat = materialById.get(link.material_id);
    if (!mat || mat.tier === "T3") continue;

    linkedTickers.add(link.ticker);
    const bucket = getOrCreateUnifiedBucket(
      mat.canonical_name,
      mat.tier,
      mat.material_group,
      {
        slug: mat.slug,
        registryCode: mat.registry_code,
        summary: mat.summary,
      }
    );
    if (!bucket) continue;

    if (link.performance_spec && bucket.specs.length < 3) {
      bucket.specs.push(link.performance_spec);
    }

    attachCompanyRole(bucket, link.ticker, link.role, "material_link", {
      name: link.product_name || mat.canonical_name,
      category: mat.material_group,
      businessUnit: link.business_unit || undefined,
      detail: link.performance_spec || link.source_section || undefined,
      endDemand: link.end_demand || undefined,
      sourceQuote: link.source_quote,
    });
  }

  // =========================================================================
  // 2. FALLBACK SOURCE: Legacy `company_products`, `commodityDrivers`, `inputs`
  //    for tickers not yet processed by the split-pass extractor
  // =========================================================================
  for (const prod of products) {
    if (prod.status !== "active" || linkedTickers.has(prod.ticker)) continue;

    // 2A. Consumed raw materials -> `consumes` on T0/T1
    for (const rm of prod.raw_materials || []) {
      if (!rm.name) continue;
      const code = rm.commodity_ref || null;
      const inp = code ? inputInfoMap.get(code) : undefined;
      const canonicalName = canonicalizeRawMaterial(rm.name, code);
      if (!canonicalName) continue;

      const tier = resolveIntrinsicTier(canonicalName, "T0");
      const bucket = getOrCreateUnifiedBucket(
        canonicalName,
        tier,
        inp?.category || (tier === "T1" ? "Refined intermediates" : "Raw Material Feedstock"),
        { registryCode: code }
      );
      if (!bucket) continue;

      attachCompanyRole(bucket, prod.ticker, "consumes", "product", {
        id: prod.id,
        name: prod.name,
        category: prod.category,
        businessUnit: prod.business_unit,
        detail: `${rm.role} (${rm.basis})`,
        endDemand: prod.downstream?.end_demand,
      });
    }

    // 2B. Sold products -> resolve intrinsic tier (T0/T1 stays T0/T1 producer; T2 goes to T2)
    const rawCat = (prod.category || prod.name || "").trim();
    let resolvedClasses = resolveAdvancedMaterialClasses(rawCat);
    if (resolvedClasses.length === 0 && prod.name) {
      resolvedClasses = resolveAdvancedMaterialClasses(prod.name);
    }

    for (const className of resolvedClasses) {
      const tier = resolveIntrinsicTier(className, "T2");
      const bucket = getOrCreateUnifiedBucket(
        className,
        tier,
        tier === "T1" ? "Refined intermediates" : "Engineered Material Class"
      );
      if (!bucket) continue;

      if (prod.advanced?.performance_spec && bucket.specs.length < 3) {
        bucket.specs.push(prod.advanced.performance_spec);
      }

      attachCompanyRole(bucket, prod.ticker, "sells", "product", {
        id: prod.id,
        name: prod.name,
        category: prod.category,
        businessUnit: prod.business_unit,
        detail: prod.advanced?.performance_spec,
        endDemand: prod.downstream?.end_demand,
      });
    }
  }

  for (const drv of commodityDrivers) {
    if (linkedTickers.has(drv.ticker)) continue;
    const code = drv.matched_input_code || null;
    if (code && macroCodeSet.has(code)) continue;
    if (
      drv.exposure_role !== "Cost Input" &&
      (!code || !inputInfoMap.has(code))
    ) {
      continue;
    }

    const inp = code ? inputInfoMap.get(code) : undefined;
    const canonicalName = canonicalizeRawMaterial(drv.commodity_name, code);
    if (!canonicalName) continue;

    const tier = resolveIntrinsicTier(canonicalName, "T0");
    const bucket = getOrCreateUnifiedBucket(
      canonicalName,
      tier,
      inp?.category || "Raw Material Feedstock",
      { registryCode: code }
    );
    if (!bucket) continue;

    if (drv.mechanism_detail && bucket.mechanismNotes.length < 3) {
      bucket.mechanismNotes.push(drv.mechanism_detail);
    }

    const role: MaterialLinkRole =
      drv.exposure_role === "Revenue Driver" ? "sells" : "consumes";
    attachCompanyRole(bucket, drv.ticker, role, "filing_driver", {
      name: `${bucket.name} (${drv.exposure_role})`,
      detail: drv.impact_direction,
    });
  }

  for (const inp of inputs) {
    if (macroCodeSet.has(inp.input_code)) continue;
    if (inp.input_code === "WPU061" || inp.input_code === "WPU066") continue;

    const canonicalName = canonicalizeRawMaterial(inp.input_name, inp.input_code);
    if (!canonicalName) continue;

    const tier = resolveIntrinsicTier(canonicalName, "T0");
    const bucket = getOrCreateUnifiedBucket(
      canonicalName,
      tier,
      inp.category || "Tracked Commodity",
      { registryCode: inp.input_code }
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
        if (
          !linkedTickers.has(eq.ticker) &&
          eq.sub_sector === rule.sub_sector &&
          !bucket.consumers.has(eq.ticker)
        ) {
          attachCompanyRole(bucket, eq.ticker, "consumes", "sector_rule", {
            name: eq.sub_sector || "Core Segment",
            detail: rule.impact_direction,
          });
        }
      }
    }
  }

  for (const eq of equities) {
    if (!eq.sub_sector || linkedTickers.has(eq.ticker)) continue;
    const resolvedClasses = resolveAdvancedMaterialClasses(eq.sub_sector);
    for (const className of resolvedClasses) {
      const tier = resolveIntrinsicTier(className, "T2");
      const bucket = getOrCreateUnifiedBucket(
        className,
        tier,
        tier === "T1" ? "Refined intermediates" : "Universe Vertical"
      );
      if (!bucket) continue;

      if (!bucket.producers.has(eq.ticker)) {
        attachCompanyRole(bucket, eq.ticker, "sells", "sector_rule", {
          name: eq.sub_sector,
          detail: "Primary advanced materials segment",
        });
      }
    }
  }

  // =========================================================================
  // 3. PARTITION BY TIER:
  //    - T2 with `sells` links -> All Advanced Materials
  //    - T0 / T1 with >=1 company link -> All Raw Materials (showing producers + consumers)
  // =========================================================================
  const sortByMcap = (a: AtlasCompanyLink, b: AtlasCompanyLink) =>
    (b.marketCapUsd ?? -1) - (a.marketCapUsd ?? -1);

  const rawMaterials: MaterialAtlasEntry[] = [];
  const advancedMaterials: MaterialAtlasEntry[] = [];

  for (const b of unifiedBuckets.values()) {
    const producers = Array.from(b.producers.values()).sort(sortByMcap);
    const consumers = Array.from(b.consumers.values()).sort(sortByMcap);
    const captiveUsers = Array.from(b.captiveUsers.values()).sort(sortByMcap);

    // Combined deduplicated companies list for card counts and search
    const combinedMap = new Map<string, AtlasCompanyLink>();
    for (const c of [...producers, ...consumers, ...captiveUsers]) {
      if (!combinedMap.has(c.ticker)) {
        combinedMap.set(c.ticker, c);
      }
    }
    const allCompanies = Array.from(combinedMap.values()).sort(sortByMcap);
    if (allCompanies.length === 0) continue;

    const inp = b.registryCode ? inputInfoMap.get(b.registryCode) : undefined;
    const spotPriceLabel = inp
      ? `${Number(inp.value).toLocaleString(undefined, {
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        })} ${inp.unit || ""}`.trim()
      : null;

    const endMarketsArr = Array.from(b.endMarkets).slice(0, 5);

    if (b.tier === "T2") {
      // "Products" shows T2 materials with `sells` links
      if (producers.length === 0) continue;
      const summary =
        b.summaryOverride ||
        b.specs[0] ||
        `Engineered T2 advanced material manufactured and supplied by ${
          producers.length
        } tracked compan${producers.length === 1 ? "y" : "ies"}${
          endMarketsArr.length > 0
            ? ` for ${endMarketsArr.slice(0, 3).join(", ")} applications`
            : ""
        }.`;

      advancedMaterials.push({
        slug: b.slug,
        kind: "advanced",
        tier: "T2",
        name: b.name,
        categoryLabel: b.categoryLabel,
        registryCode: b.registryCode,
        spotPriceLabel,
        summary,
        endMarkets: endMarketsArr,
        companies: producers,
        producers,
        consumers,
        captiveUsers,
      });
    } else if (b.tier === "T0" || b.tier === "T1") {
      // T0 and T1 materials display both Producers (`sells`) and Consumers (`consumes`)
      const summary =
        b.summaryOverride ||
        (b.registryCode && CURATED_SUMMARIES[b.registryCode]) ||
        b.mechanismNotes[0] ||
        `${
          b.tier === "T1"
            ? "Refined intermediate (T1)"
            : "Primary raw feedstock (T0)"
        } tracked across ${allCompanies.length} compan${
          allCompanies.length === 1 ? "y" : "ies"
        }${
          producers.length > 0 && consumers.length > 0
            ? ` (${producers.length} producer${producers.length === 1 ? "" : "s"}, ${consumers.length} consumer${consumers.length === 1 ? "" : "s"})`
            : ""
        }.`;

      rawMaterials.push({
        slug: b.slug,
        kind: "raw",
        tier: b.tier,
        name: b.name,
        categoryLabel: b.categoryLabel,
        registryCode: b.registryCode,
        spotPriceLabel,
        summary,
        endMarkets: endMarketsArr,
        companies: allCompanies,
        producers,
        consumers,
        captiveUsers,
      });
    }
  }

  rawMaterials.sort(
    (a, b) =>
      b.companies.length - a.companies.length || a.name.localeCompare(b.name)
  );
  advancedMaterials.sort(
    (a, b) =>
      b.companies.length - a.companies.length || a.name.localeCompare(b.name)
  );

  return { rawMaterials, advancedMaterials };
}
