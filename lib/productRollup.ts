export interface ProductRawMaterial {
  name: string;
  commodity_ref: string | null;
  role: string;
  basis: "disclosed" | "inferred";
}

export interface ProductDownstream {
  process_step: string;
  end_product: string;
  end_demand: string;
  demand_proxy_ref: string | null;
  customers: string[] | null;
}

export interface CompanyProduct {
  id: string;
  ticker: string;
  name: string;
  category: string;
  business_unit: string;
  status: "active" | "divested";
  as_of: string;
  advanced: {
    performance_spec: string;
    qualification_months: number | null;
    switching_barrier: string;
  };
  raw_materials: ProductRawMaterial[];
  downstream: ProductDownstream;
  pricing_structure: string | null;
  revenue: {
    basis: string;
    share_pct: number | null;
  };
  peers: string[];
  src: {
    type: "annual_report" | "filing" | "product_page" | "inferred";
    ref: string | null;
  };
  conf: "high" | "med" | "low";
}

export interface ExcludedProduct {
  ticker: string;
  name: string;
  reason: string;
}

export interface CompanyProductProfile {
  ticker: string;
  note: string | null;
}

export interface RolledUpDrivers {
  activeProductCount: number;
  costInputCodes: string[];      // Unique registry IDs from active raw_materials
  demandProxyCodes: string[];    // Unique registry IDs from active downstream.demand_proxy_ref
  unloggedMaterials: string[];   // Unique raw_materials where commodity_ref === null
  isProductDerived: boolean;     // True when company has active products
}

export interface CommodityReverseHit {
  ticker: string;
  products: CompanyProduct[];
  matchRoles: ("cost_input" | "demand_proxy")[];
}

/**
 * Derives company-level cost_inputs and demand_proxies strictly from status === 'active' products.
 * Divested products are excluded from all counts and driver rollups.
 */
export function rollupCompanyProducts(
  products: CompanyProduct[],
  validRegistryCodes: Set<string>
): RolledUpDrivers {
  const active = products.filter((p) => p.status === "active");
  const costSet = new Set<string>();
  const demandSet = new Set<string>();
  const unloggedSet = new Set<string>();

  for (const prod of active) {
    for (const rm of prod.raw_materials || []) {
      if (rm.commodity_ref && validRegistryCodes.has(rm.commodity_ref)) {
        costSet.add(rm.commodity_ref);
      } else if (!rm.commodity_ref) {
        unloggedSet.add(rm.name);
      }
    }
    const proxy = prod.downstream?.demand_proxy_ref;
    if (proxy && validRegistryCodes.has(proxy)) {
      demandSet.add(proxy);
    }
  }

  return {
    activeProductCount: active.length,
    costInputCodes: Array.from(costSet),
    demandProxyCodes: Array.from(demandSet),
    unloggedMaterials: Array.from(unloggedSet),
    isProductDerived: active.length > 0,
  };
}

/**
 * Reverse index: commodity_ref -> matching active products -> companies.
 */
export function buildCommodityReverseIndex(
  allProducts: CompanyProduct[],
  validRegistryCodes: Set<string>
): Record<string, Record<string, CommodityReverseHit>> {
  const index: Record<string, Record<string, CommodityReverseHit>> = {};

  const recordHit = (
    code: string,
    prod: CompanyProduct,
    role: "cost_input" | "demand_proxy"
  ) => {
    if (!validRegistryCodes.has(code)) return;
    if (!index[code]) index[code] = {};
    if (!index[code][prod.ticker]) {
      index[code][prod.ticker] = {
        ticker: prod.ticker,
        products: [],
        matchRoles: [],
      };
    }
    const entry = index[code][prod.ticker];
    if (!entry.products.some((p) => p.id === prod.id)) {
      entry.products.push(prod);
    }
    if (!entry.matchRoles.includes(role)) {
      entry.matchRoles.push(role);
    }
  };

  for (const prod of allProducts) {
    if (prod.status !== "active") continue;
    for (const rm of prod.raw_materials || []) {
      if (rm.commodity_ref) {
        recordHit(rm.commodity_ref, prod, "cost_input");
      }
    }
    if (prod.downstream?.demand_proxy_ref) {
      recordHit(prod.downstream.demand_proxy_ref, prod, "demand_proxy");
    }
  }

  return index;
}

/**
 * Reverse index: product category -> active products & peer tickers across companies.
 */
export function buildCategoryPeerIndex(allProducts: CompanyProduct[]): Record<
  string,
  { tickers: Set<string>; products: CompanyProduct[] }
> {
  const index: Record<string, { tickers: Set<string>; products: CompanyProduct[] }> = {};

  for (const prod of allProducts) {
    if (prod.status !== "active") continue;
    if (!index[prod.category]) {
      index[prod.category] = { tickers: new Set<string>(), products: [] };
    }
    index[prod.category].tickers.add(prod.ticker);
    index[prod.category].products.push(prod);
    for (const peerTicker of prod.peers || []) {
      index[prod.category].tickers.add(peerTicker);
    }
  }

  return index;
}
