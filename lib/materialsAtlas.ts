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
  target_product?: string | null;
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
  sourceOrigin: "material_link";
}

export interface MaterialAtlasEntry {
  slug: string;
  kind: "raw" | "advanced" | "end_market";
  tier: MaterialTier | "EM";
  name: string;
  categoryLabel: string;
  registryCode: string | null;
  spotPriceLabel: string | null;
  summary: string;
  endMarkets: string[];
  linkedMaterialClasses?: string[];
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

interface CanonicalEndMarketSpec {
  name: string;
  categoryLabel: string;
  registryCode: string | null;
  summary: string;
  patterns: RegExp[];
}

const CANONICAL_END_MARKETS: CanonicalEndMarketSpec[] = [
  {
    name: "Semiconductors and Wafer Fabrication",
    categoryLabel: "Microelectronics & Fabs",
    registryCode: "SMH",
    summary:
      "Front-end logic, DRAM/NAND memory, and compound semiconductor wafer fabrication requiring ultra-pure substrates, photoresists, CMP slurries, and specialty process gases.",
    patterns: [
      /\b(semiconductor|wafer|foundry|fab|logic|memory|dram|nand|lithography|euv|ic\b|integrated\s+circuit|microelectronics|chipmaking)\b/i,
    ],
  },
  {
    name: "AI Data Center and High-Speed Networking",
    categoryLabel: "Compute & Optical Interconnects",
    registryCode: "SMH",
    summary:
      "Hyperscale AI server accelerators, HBM packaging, 800G/1.6T optical transceivers, and ultra-low-loss PCB laminates.",
    patterns: [
      /\b(ai\b|artificial\s+intelligence|data\s*center|hyperscale|server|hbm|high[- ]bandwidth|800g|1\.6t|optical\s+(fiber|network|transceiver)|telecom|5g|rf\b|mmwave)\b/i,
    ],
  },
  {
    name: "Consumer and Industrial Electronics",
    categoryLabel: "Displays, PCBs & Devices",
    registryCode: null,
    summary:
      "OLED/LCD display panels, multilayer ceramic capacitors (MLCCs), flexible printed circuits, and thermal management assemblies across consumer and industrial hardware.",
    patterns: [
      /\b(electronics?|display|oled|lcd|smartphone|handset|pc\b|pcb|printed\s+circuit|mlcc|capacitor|consumer\s+device)\b/i,
    ],
  },
  {
    name: "EV and Energy Storage Batteries",
    categoryLabel: "Electrified Mobility & Storage",
    registryCode: "LIT",
    summary:
      "Lithium-ion traction batteries and stationary grid storage systems requiring high-nickel/LFP cathodes, synthetic/natural graphite anodes, electrolytes, separators, and ultra-thin copper foil.",
    patterns: [
      /\b(ev\b|electric\s+vehicle|battery|batteries|lithium[- ]ion|cathode|anode|electrolyte|energy\s+storage|ess\b|gigafactory)\b/i,
    ],
  },
  {
    name: "Aerospace and Defense",
    categoryLabel: "Airframes, Propulsion & Radar",
    registryCode: "ITA",
    summary:
      "Commercial aircraft structures, jet-engine hot sections, hypersonics, satellite solar arrays, and defense RF/sensor systems requiring carbon fiber prepregs, superalloys, and titanium.",
    patterns: [
      /\b(aerospace|defense|defence|aircraft|aviation|airframe|jet\s+engine|turbine|space|satellite|missile|radar|military)\b/i,
    ],
  },
  {
    name: "Electrification, Grid and Clean Energy",
    categoryLabel: "Power Transmission, Solar & Hydrogen",
    registryCode: "URA",
    summary:
      "High-voltage grid transformers, wind turbine permanent magnets, photovoltaic solar modules, nuclear reactors, and green hydrogen PEM electrolyzers.",
    patterns: [
      /\b(grid|electrification|power\s+(generation|transmission|conversion)|solar|photovoltaic|pv\b|wind|hydrogen|electrolyzer|fuel\s+cell|nuclear|renewable)\b/i,
    ],
  },
  {
    name: "Automotive and Mobility",
    categoryLabel: "Powertrain, Thermal & Lightweighting",
    registryCode: null,
    summary:
      "Internal combustion emissions catalysts, 800V SiC power inverters, lightweight structural alloys, and high-heat under-hood polymers.",
    patterns: [
      /\b(automotive|mobility|vehicle|auto\b|transportation|emissions|catalytic|powertrain)\b/i,
    ],
  },
  {
    name: "Medical Devices and Healthcare",
    categoryLabel: "Implantables & Biocompatibles",
    registryCode: "IHI",
    summary:
      "USP Class VI implantable polymers, bioresorbable orthopedic fixation, cardiovascular catheters, and medical titanium/nitinol components.",
    patterns: [
      /\b(medical|healthcare|biomedical|implant|surgical|catheter|orthopedic|life\s+science|pharma)\b/i,
    ],
  },
  {
    name: "Industrial and Chemical Processing",
    categoryLabel: "Refining, Tooling & Infrastructure",
    registryCode: "INDPRO",
    summary:
      "Petrochemical refining catalysts, corrosion-resistant fluoropolymer linings, specialty steel tooling, and industrial gas applications.",
    patterns: [
      /\b(industrial|chemical|refining|petrochemical|manufacturing|construction|infrastructure|tooling|oil\s+and\s+gas|energy)\b/i,
    ],
  },
];

function slugify(str: string): string {
  return str
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export function resolveCanonicalEndMarket(
  rawEndDemand: string | null | undefined
): CanonicalEndMarketSpec {
  const text = (rawEndDemand || "").trim();
  if (text) {
    for (const spec of CANONICAL_END_MARKETS) {
      if (spec.patterns.some((re) => re.test(text))) {
        return spec;
      }
    }
  }
  return CANONICAL_END_MARKETS[CANONICAL_END_MARKETS.length - 1];
}

export function buildMaterialsAtlas({
  equities,
  inputs,
  materials = [],
  materialLinks = [],
}: {
  equities: MinimalEquity[];
  products?: CompanyProduct[];
  inputs: MinimalInput[];
  commodityDrivers?: unknown[];
  sensitivities?: unknown[];
  macroCodeSet?: Set<string>;
  inputNameMap?: Record<string, string>;
  materials?: MaterialRecord[];
  materialLinks?: CompanyMaterialLinkRow[];
}): {
  rawMaterials: MaterialAtlasEntry[];
  advancedMaterials: MaterialAtlasEntry[];
  endMarkets: MaterialAtlasEntry[];
} {
  const equityMap = new Map<string, MinimalEquity>();
  for (const eq of equities) {
    equityMap.set(eq.ticker, eq);
  }

  const inputInfoMap = new Map<string, MinimalInput>();
  for (const inp of inputs) {
    inputInfoMap.set(inp.input_code, inp);
  }

  interface UnifiedBucket {
    slug: string;
    name: string;
    tier: MaterialTier;
    categoryLabel: string;
    registryCode: string | null;
    summaryOverride: string | null;
    specs: string[];
    endMarkets: Set<string>;
    producers: Map<string, AtlasCompanyLink>;
    consumers: Map<string, AtlasCompanyLink>;
    captiveUsers: Map<string, AtlasCompanyLink>;
  }

  interface EndMarketBucket {
    slug: string;
    name: string;
    categoryLabel: string;
    registryCode: string | null;
    summary: string;
    materialClasses: Set<string>;
    producers: Map<string, AtlasCompanyLink>;
  }

  const bucketsByMaterialId = new Map<number, UnifiedBucket>();
  const endMarketBuckets = new Map<string, EndMarketBucket>();
  const materialById = new Map<number, MaterialRecord>();
  for (const m of materials) {
    materialById.set(m.id, m);
  }

  for (const link of materialLinks) {
    const mat = materialById.get(link.material_id);
    if (!mat || mat.tier === "T3") continue;

    const eq = equityMap.get(link.ticker);
    if (!eq) continue;

    let bucket = bucketsByMaterialId.get(mat.id);
    if (!bucket) {
      bucket = {
        slug: mat.slug,
        name: mat.canonical_name,
        tier: mat.tier,
        categoryLabel: mat.material_group,
        registryCode: mat.registry_code,
        summaryOverride: mat.summary,
        specs: [],
        endMarkets: new Set<string>(),
        producers: new Map<string, AtlasCompanyLink>(),
        consumers: new Map<string, AtlasCompanyLink>(),
        captiveUsers: new Map<string, AtlasCompanyLink>(),
      };
      bucketsByMaterialId.set(mat.id, bucket);
    }

    const canonicalEm = link.end_demand
      ? resolveCanonicalEndMarket(link.end_demand)
      : null;

    if (link.performance_spec && bucket.specs.length < 3) {
      bucket.specs.push(link.performance_spec);
    }
    if (canonicalEm) {
      bucket.endMarkets.add(canonicalEm.name);
    }

    const targetMap =
      link.role === "sells"
        ? bucket.producers
        : link.role === "consumes"
        ? bucket.consumers
        : bucket.captiveUsers;

    let compLink = targetMap.get(link.ticker);
    if (!compLink) {
      compLink = {
        ticker: eq.ticker,
        companyName: eq.company_name,
        subSector: eq.sub_sector,
        country: eq.country,
        marketCapUsd: eq.market_cap_usd,
        contractType: eq.contract_type,
        role: link.role,
        linkedProducts: [],
        sourceOrigin: "material_link",
      };
      targetMap.set(link.ticker, compLink);
    }

    const prodLabel = link.product_name || mat.canonical_name;
    const exists = compLink.linkedProducts.some(
      (p) => p.name.toLowerCase() === prodLabel.toLowerCase()
    );
    if (!exists) {
      compLink.linkedProducts.push({
        name: prodLabel,
        category: mat.canonical_name,
        businessUnit: link.business_unit || undefined,
        detail: link.performance_spec || link.source_section || undefined,
        endDemand: canonicalEm?.name || link.end_demand || undefined,
        sourceQuote: link.source_quote,
      });
    }

   // Populate End-Market directory strictly with products tied to that specific end-market
    if (
      mat.tier === "T2" &&
      (link.role === "sells" || link.role === "makes_captive")
    ) {
      // Only group under an End-Market if this specific link declares an end_demand
      // (or fallback to mat.material_group if link.end_demand is null/empty)
      const rawTargetDemand = (link.end_demand || "").trim();
      const emSpec = rawTargetDemand
        ? resolveCanonicalEndMarket(rawTargetDemand)
        : resolveCanonicalEndMarket(mat.material_group);

      const emSlug = `em-${slugify(emSpec.name)}`;
      let emBucket = endMarketBuckets.get(emSlug);
      if (!emBucket) {
        emBucket = {
          slug: emSlug,
          name: emSpec.name,
          categoryLabel: emSpec.categoryLabel,
          registryCode: emSpec.registryCode,
          summary: emSpec.summary,
          materialClasses: new Set<string>(),
          producers: new Map<string, AtlasCompanyLink>(),
        };
        endMarketBuckets.set(emSlug, emBucket);
      }

      emBucket.materialClasses.add(mat.canonical_name);

      let emComp = emBucket.producers.get(link.ticker);
      if (!emComp) {
        emComp = {
          ticker: eq.ticker,
          companyName: eq.company_name,
          subSector: eq.sub_sector,
          country: eq.country,
          marketCapUsd: eq.market_cap_usd,
          contractType: eq.contract_type,
          role: link.role,
          linkedProducts: [],
          sourceOrigin: "material_link",
        };
        emBucket.producers.set(link.ticker, emComp);
      }

      const emProdExists = emComp.linkedProducts.some(
        (p) =>
          p.name.toLowerCase() === prodLabel.toLowerCase() &&
          (p.category || "").toLowerCase() === mat.canonical_name.toLowerCase()
      );
      if (!emProdExists) {
        emComp.linkedProducts.push({
          name: prodLabel,
          category: mat.canonical_name,
          businessUnit: link.business_unit || undefined,
          detail: mat.canonical_name,
          endDemand: emSpec.name,
          sourceQuote: link.source_quote,
        });
      }
    }
  }

  const sortByMcap = (a: AtlasCompanyLink, b: AtlasCompanyLink) =>
    (b.marketCapUsd ?? -1) - (a.marketCapUsd ?? -1);

  const rawMaterials: MaterialAtlasEntry[] = [];
  const advancedMaterials: MaterialAtlasEntry[] = [];

  for (const b of bucketsByMaterialId.values()) {
    const producers = Array.from(b.producers.values()).sort(sortByMcap);
    const consumers = Array.from(b.consumers.values()).sort(sortByMcap);
    const captiveUsers = Array.from(b.captiveUsers.values()).sort(sortByMcap);

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

    const endMarketsArr = Array.from(b.endMarkets).slice(0, 6);

    if (b.tier === "T2") {
      if (producers.length === 0) continue;
      const summary =
        b.summaryOverride ||
        b.specs[0] ||
        `Engineered T2 advanced material manufactured and supplied by ${
          producers.length
        } tracked compan${producers.length === 1 ? "y" : "ies"}.`;

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
      const summary =
        b.summaryOverride ||
        `${
          b.tier === "T1"
            ? "Refined intermediate (T1)"
            : "Primary raw feedstock (T0)"
        } tracked across ${allCompanies.length} compan${
          allCompanies.length === 1 ? "y" : "ies"
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

  const endMarkets: MaterialAtlasEntry[] = Array.from(endMarketBuckets.values())
    .filter((em) => em.producers.size > 0)
    .map((em) => {
      const producers = Array.from(em.producers.values()).sort(sortByMcap);
      const inp = em.registryCode ? inputInfoMap.get(em.registryCode) : undefined;
      const spotPriceLabel = inp
        ? `${Number(inp.value).toLocaleString(undefined, {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
          })} ${inp.unit || ""}`.trim()
        : null;

      return {
        slug: em.slug,
        kind: "end_market" as const,
        tier: "EM" as const,
        name: em.name,
        categoryLabel: em.categoryLabel,
        registryCode: em.registryCode,
        spotPriceLabel,
        summary: em.summary,
        endMarkets: [],
        linkedMaterialClasses: Array.from(em.materialClasses).sort(),
        companies: producers,
        producers,
        consumers: [],
        captiveUsers: [],
      };
    })
    .sort(
      (a, b) =>
        b.companies.length - a.companies.length || a.name.localeCompare(b.name)
    );

  rawMaterials.sort(
    (a, b) =>
      b.companies.length - a.companies.length || a.name.localeCompare(b.name)
  );
  advancedMaterials.sort(
    (a, b) =>
      b.companies.length - a.companies.length || a.name.localeCompare(b.name)
  );

  return { rawMaterials, advancedMaterials, endMarkets };
}
