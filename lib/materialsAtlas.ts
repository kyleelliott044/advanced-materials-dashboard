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
  sourceOrigin: "material_link";
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

  const bucketsByMaterialId = new Map<number, UnifiedBucket>();
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

    if (link.performance_spec && bucket.specs.length < 3) {
      bucket.specs.push(link.performance_spec);
    }
    if (link.end_demand) {
      bucket.endMarkets.add(link.end_demand);
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
        category: mat.material_group,
        businessUnit: link.business_unit || undefined,
        detail: link.performance_spec || link.source_section || undefined,
        endDemand: link.end_demand || undefined,
        sourceQuote: link.source_quote,
      });
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

    const endMarketsArr = Array.from(b.endMarkets).slice(0, 5);

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
