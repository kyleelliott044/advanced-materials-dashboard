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
    "European TTF natural gas benchmark dictating European chemical plant competitiveness, energy surcharges, and regional ammonia/polymer margins.",
  WPU101:
    "Benchmark ferrous and alloy steel input for clean specialty bar, seamless mechanical tubing, and bearing-grade aerospace/industrial steels.",
  WPU061:
    "Base industrial chemical feedstocks (chlor-alkali, acids, solvents) converted into high-purity semiconductor wet etchants and specialty formulations.",
  WPU066:
    "Base thermoplastic and thermoset polymer resins compounded into high-temperature engineering plastics, fluoropolymers, and dielectric films.",
  NDPR_CN:
    "Neodymium-praseodymium rare earth oxide refined into NdFeB permanent magnets for EV traction motors, wind turbines, and defense precision actuators.",
  LICARB_CN:
    "Battery-grade lithium carbonate and lithium hydroxide monohydrate serving as the primary lithium source for EV cathodes and LiPF6 electrolyte salts.",
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
    .replace(
      /\b(high[- ]purity|electronic[- ]grade|battery[- ]grade|refined|raw|feedstock|ingot|spot|futures)\b/g,
      ""
    )
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
  inputNameMap,
}: {
  equities: MinimalEquity[];
  products: CompanyProduct[];
  inputs: MinimalInput[];
  commodityDrivers: MinimalDriver[];
  sensitivities: MinimalSensitivity[];
  macroCodeSet: Set<string>;
  inputNameMap: Record<string, string>;
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
  ): RawBucket => {
    const key = registryCode
      ? `code:${registryCode}`
      : `name:${normalizeMaterialKey(rawName) || rawName.toLowerCase()}`;
    let bucket = rawBuckets.get(key);
    if (!bucket) {
      const canonicalName =
        (registryCode && inputNameMap[registryCode]) || rawName.trim();
      bucket = {
        name: canonicalName,
        categoryLabel,
        registryCode,
        endMarkets: new Set<string>(),
        mechanismNotes: [],
        companies: new Map<string, AtlasCompanyLink>(),
      };
      rawBuckets.set(key, bucket);
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
        inp?.category || "Feedstock / Raw Material"
      );
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
      inp?.category || "Filing Commodity Input"
    );
    if (drv.mechanism_detail && bucket.mechanismNotes.length < 3) {
      bucket.mechanismNotes.push(drv.mechanism_detail);
    }
    attachRawCompany(bucket, drv.ticker, "filing_driver", {
      name: `${drv.commodity_name} (${drv.exposure_role})`,
      detail: drv.impact_direction,
    });
  }

  for (const inp of inputs) {
    if (macroCodeSet.has(inp.input_code)) continue;
    const bucket = getOrCreateRawBucket(
      inp.input_name,
      inp.input_code,
      inp.category || "Tracked Commodity"
    );
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
        `Industrial raw material / chemical feedstock consumed across ${
          sortedCompanies.length
        } tracked advanced materials producer${
          sortedCompanies.length === 1 ? "" : "s"
        }${
          endMarketsArr.length > 0
            ? ` serving ${endMarketsArr.slice(0, 3).join(", ")}`
            : ""
        }.`;

      return {
        slug: `raw-${slugify(b.registryCode || b.name)}`,
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
    const key = normalizeMaterialKey(matName) || matName.toLowerCase();
    let bucket = advBuckets.get(key);
    if (!bucket) {
      bucket = {
        name: matName.trim(),
        categoryLabel,
        specs: [],
        endMarkets: new Set<string>(),
        companies: new Map<string, AtlasCompanyLink>(),
      };
      advBuckets.set(key, bucket);
    }
    return bucket;
  };

  for (const prod of products) {
    if (prod.status !== "active") continue;
    const catName = prod.category || prod.name;
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
    if (!eq.sub_sector) continue;
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
