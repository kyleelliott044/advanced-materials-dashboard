"use client";

import { useState, useMemo, useEffect } from "react";
import {
  CompanyProduct,
  ExcludedProduct,
  CompanyProductProfile,
  rollupCompanyProducts,
  buildCommodityReverseIndex,
  buildCategoryPeerIndex,
  RolledUpDrivers,
} from "../lib/productRollup";

export interface EquityRow {
  ticker: string;
  company_name: string;
  sub_sector: string | null;
  contract_type: string | null;
  qualification_years: string | number | null;
  adv_mat_rev_pct: number | null;
  country: string | null;
  market_cap_usd: number | null;
}

export interface RawInputRow {
  input_code: string;
  input_name: string;
  category: string | null;
  unit: string | null;
  observation_date: string;
  value: string | number;
  is_proxy?: boolean;
  source_note?: string | null;
}

export interface SensitivityRow {
  sub_sector: string;
  input_code: string;
  relationship_type: string;
  impact_direction: string;
  mechanism_note: string;
}

export interface FilingRow {
  id: number;
  ticker: string;
  company_name?: string;
  sub_sector?: string;
  filing_type: string;
  filing_date: string;
  fiscal_period?: string | null;
  headline: string;
  executive_summary: string;
  theme_tags: string[];
  pricing_and_margins: string | null;
  capex_and_backlog: string | null;
  key_risks: string | null;
  source_url: string | null;
}

export interface MacroNarrativeRow {
  generated_date: string;
  overall_regime: string;
  executive_brief: string;
  ai_theme_summary: string;
  aerospace_theme_summary: string;
  commodities_theme_summary: string;
  electrification_battery_summary: string;
}

export interface SupplyChainRow {
  ticker: string;
  direction: "upstream_supplier" | "downstream_customer" | "competitor" | string;
  counterparty_name: string;
  is_named_entity: boolean;
  material_or_product: string;
  is_sole_source: boolean;
  exposure_pct: number | null;
  source_filing_type: string | null;
  source_filing_date: string | null;
  source_quote: string | null;
}

export interface CommodityDriverRow {
  ticker: string;
  commodity_name: string;
  matched_input_code: string | null;
  exposure_role: string;
  impact_direction: string;
  pass_through_lag: string | null;
  cogs_or_rev_share_pct: number | null;
  mechanism_detail: string;
  source_filing_type: string | null;
  source_filing_date: string | null;
  source_quote: string | null;
}

type SortOption =
  | "mcap_desc"
  | "mcap_asc"
  | "rev_desc"
  | "rev_asc"
  | "country_asc"
  | "moat_desc"
  | "products_desc"
  | "ticker_asc";

const CONTRACT_BADGES: Record<string, { label: string; style: string }> = {
  take_or_pay: {
    label: "Take-or-Pay",
    style: "bg-emerald-500/10 text-emerald-400 border-emerald-500/30",
  },
  pass_through: {
    label: "Pass-Through",
    style: "bg-sky-500/10 text-sky-400 border-sky-500/30",
  },
  fixed_spread: {
    label: "Fixed Spread",
    style: "bg-amber-500/10 text-amber-400 border-amber-500/30",
  },
};

const CORE_THEMES = [
  "AI Infrastructure",
  "Aerospace & Defense",
  "Electrification & Grid",
  "EV & Batteries",
  "Semiconductors",
  "Medical Devices",
  "Industrial Recovery",
  "Commodity Headwind",
  "Pricing Power",
];

const FALLBACK_INPUT_NAMES: Record<string, string> = {
  "HG=F": "Copper",
  "NG=F": "Natural Gas",
  "CL=F": "WTI Crude Oil",
  "BZ=F": "Brent Crude Oil",
  "ALI=F": "Aluminum",
  "PL=F": "Platinum",
  "SI=F": "Silver",
  PNICKUSDM: "Global Nickel",
  PCOBAUSDM: "Global Cobalt",
  PNGASEUUSDM: "European Natural Gas",
  WPU101: "Iron & Steel",
  WPU061: "Industrial Chemicals",
  WPU066: "Plastic Resins",
  INDPRO: "US Industrial Production",
  PCUOMFGOMFG: "Manufacturing PPI",
  DGS10: "10-Year Treasury Yield",
  SMH: "Semiconductor Wafer Demand",
  ITA: "Aerospace & Defense",
  LIT: "Lithium & Battery Tech",
  REMX: "Rare Earth Metals",
  URA: "Uranium & Nuclear",
  SLX: "Specialty Steel",
  IHI: "Medical Devices",
  NDPR_CN: "NdPr Rare Earth Oxide",
  LICARB_CN: "Lithium Carbonate",
  GALLIUM_9999: "Refined Gallium",
  GERMANIUM_INGOT: "Germanium Ingot",
  POLYSILICON_N: "N-Type Polysilicon",
  SPHERICAL_GRAPHITE: "Spherical Graphite",
  COBALT_SULFATE: "Cobalt Sulfate",
  CARBON_FIBER_T700: "Carbon Fiber Tow",
};

function formatMarketCap(usd: number | null): string {
  if (usd == null || usd <= 0) return "—";
  if (usd >= 1e12) return `$${(usd / 1e12).toFixed(2)}T`;
  if (usd >= 1e9) return `$${(usd / 1e9).toFixed(1)}B`;
  if (usd >= 1e6) return `$${(usd / 1e6).toFixed(0)}M`;
  return `$${usd.toLocaleString()}`;
}

export default function DashboardClient({
  equities,
  inputs,
  sensitivities,
  filings,
  narrative,
  supplyChain = [],
  commodityDrivers = [],
  products = [],
  excludedProducts = [],
  productProfiles = [],
}: {
  equities: EquityRow[];
  inputs: RawInputRow[];
  sensitivities: SensitivityRow[];
  filings: FilingRow[];
  narrative: MacroNarrativeRow | null;
  supplyChain?: SupplyChainRow[];
  commodityDrivers?: CommodityDriverRow[];
  products?: CompanyProduct[];
  excludedProducts?: ExcludedProduct[];
  productProfiles?: CompanyProductProfile[];
}) {
  const [activeTab, setActiveTab] = useState<"screener" | "feed">("screener");
  const [narrativeTab, setNarrativeTab] = useState<
    "executive" | "ai" | "aero" | "commodities" | "ev"
  >("executive");
  const [search, setSearch] = useState("");
  const [sectorFilter, setSectorFilter] = useState("ALL");
  const [countryFilter, setCountryFilter] = useState("ALL");
  const [contractFilter, setContractFilter] = useState("ALL");
  const [sortBy, setSortBy] = useState<SortOption>("mcap_desc");
  const [selectedCommodity, setSelectedCommodity] = useState<string | null>(null);
  const [selectedProductCategory, setSelectedProductCategory] = useState<string | null>(null);
  const [minQualYears, setMinQualYears] = useState(0);
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  const [feedThemeFilter, setFeedThemeFilter] = useState("ALL");
  const [isLight, setIsLight] = useState(false);

  useEffect(() => {
    const saved = localStorage.getItem("dashboard-theme");
    if (saved === "light") {
      document.documentElement.classList.add("light-mode");
      setIsLight(true);
    }
  }, []);

  const toggleTheme = () => {
    const next = !isLight;
    setIsLight(next);
    document.documentElement.classList.toggle("light-mode", next);
    localStorage.setItem("dashboard-theme", next ? "light" : "dark");
  };

  // Registry ID (input_code) -> display name map & valid registry ID set
  const { inputNameMap, validRegistryCodes } = useMemo(() => {
    const map: Record<string, string> = { ...FALLBACK_INPUT_NAMES };
    const codes = new Set<string>(Object.keys(FALLBACK_INPUT_NAMES));
    for (const item of inputs) {
      map[item.input_code] = item.input_name;
      codes.add(item.input_code);
    }
    return { inputNameMap: map, validRegistryCodes: codes };
  }, [inputs]);

  const getCommodityDisplayName = (code: string) =>
    inputNameMap[code] || code;

  // Group products, excluded items, and notes by ticker
  const tickerProductsMap = useMemo(() => {
    const map: Record<string, CompanyProduct[]> = {};
    for (const p of products) {
      if (!map[p.ticker]) map[p.ticker] = [];
      map[p.ticker].push(p);
    }
    return map;
  }, [products]);

  const tickerExcludedMap = useMemo(() => {
    const map: Record<string, ExcludedProduct[]> = {};
    for (const ex of excludedProducts) {
      if (!map[ex.ticker]) map[ex.ticker] = [];
      map[ex.ticker].push(ex);
    }
    return map;
  }, [excludedProducts]);

  const tickerNoteMap = useMemo(() => {
    const map: Record<string, string> = {};
    for (const prof of productProfiles) {
      if (prof.note) map[prof.ticker] = prof.note;
    }
    return map;
  }, [productProfiles]);

  // Product rollup per company (strictly status === 'active'; supersedes legacy rules when present)
  const tickerRollupMap = useMemo(() => {
    const map: Record<string, RolledUpDrivers> = {};
    for (const eq of equities) {
      const prods = tickerProductsMap[eq.ticker] || [];
      map[eq.ticker] = rollupCompanyProducts(prods, validRegistryCodes);
    }
    return map;
  }, [equities, tickerProductsMap, validRegistryCodes]);

  // Reverse indexes: commodity_ref -> products -> companies & category -> peers
  const commodityReverseIndex = useMemo(
    () => buildCommodityReverseIndex(products, validRegistryCodes),
    [products, validRegistryCodes]
  );

  const categoryPeerIndex = useMemo(
    () => buildCategoryPeerIndex(products),
    [products]
  );

  const sectors = useMemo(() => {
    const unique = new Set(
      equities.map((e) => e.sub_sector).filter((s): s is string => Boolean(s))
    );
    return ["ALL", ...Array.from(unique).sort()];
  }, [equities]);

  const countries = useMemo(() => {
    const unique = new Set(
      equities.map((e) => e.country).filter((c): c is string => Boolean(c))
    );
    return ["ALL", ...Array.from(unique).sort()];
  }, [equities]);

  const feedThemes = useMemo(() => {
    const unique = new Set<string>(CORE_THEMES);
    for (const f of filings) {
      for (const tag of f.theme_tags || []) {
        unique.add(tag);
      }
    }
    return ["ALL", ...Array.from(unique)];
  }, [filings]);

  const sectorSensitivitiesMap = useMemo(() => {
    const map: Record<string, SensitivityRow[]> = {};
    for (const s of sensitivities) {
      if (!map[s.sub_sector]) map[s.sub_sector] = [];
      map[s.sub_sector].push(s);
    }
    return map;
  }, [sensitivities]);

  const tickerDriversMap = useMemo(() => {
    const map: Record<string, CommodityDriverRow[]> = {};
    for (const d of commodityDrivers) {
      if (!map[d.ticker]) map[d.ticker] = [];
      map[d.ticker].push(d);
    }
    return map;
  }, [commodityDrivers]);

  const tickerFilingsMap = useMemo(() => {
    const map: Record<string, FilingRow[]> = {};
    for (const f of filings) {
      if (!map[f.ticker]) map[f.ticker] = [];
      map[f.ticker].push(f);
    }
    return map;
  }, [filings]);

  const coveredCompanyCount = useMemo(
    () => Object.keys(tickerFilingsMap).length,
    [tickerFilingsMap]
  );

  const tickerSupplyChainMap = useMemo(() => {
    const map: Record<string, SupplyChainRow[]> = {};
    for (const sc of supplyChain) {
      if (!map[sc.ticker]) map[sc.ticker] = [];
      map[sc.ticker].push(sc);
    }
    return map;
  }, [supplyChain]);

  // Unified company-level cost_inputs & demand_proxies (rolled up from active products first; legacy fallback only if no products exist)
  const companyDriversByRole = useMemo(() => {
    const map: Record<
      string,
      {
        costInputs: { code: string | null; label: string; detail?: string }[];
        demandProxies: { code: string | null; label: string; detail?: string }[];
        fromProductRollup: boolean;
      }
    > = {};

    for (const eq of equities) {
      const rollup = tickerRollupMap[eq.ticker];
      if (rollup && rollup.isProductDerived) {
        map[eq.ticker] = {
          costInputs: rollup.costInputCodes.map((code) => ({
            code,
            label: getCommodityDisplayName(code),
          })),
          demandProxies: rollup.demandProxyCodes.map((code) => ({
            code,
            label: getCommodityDisplayName(code),
          })),
          fromProductRollup: true,
        };
        continue;
      }

      // Fallback for companies not yet decomposed into products
      const aiDrivers = tickerDriversMap[eq.ticker] || [];
      if (aiDrivers.length > 0) {
        const costInputs = aiDrivers
          .filter((d) => d.exposure_role === "Cost Input")
          .map((d) => ({
            code: d.matched_input_code,
            label: d.commodity_name,
            detail: d.mechanism_detail,
          }));
        const demandProxies = aiDrivers
          .filter((d) => d.exposure_role !== "Cost Input")
          .map((d) => ({
            code: d.matched_input_code,
            label: d.commodity_name,
            detail: d.mechanism_detail,
          }));
        map[eq.ticker] = { costInputs, demandProxies, fromProductRollup: false };
      } else {
        const subRules = sectorSensitivitiesMap[eq.sub_sector || ""] || [];
        const costInputs = subRules
          .filter((r) => r.relationship_type === "Cost Input")
          .map((r) => ({
            code: r.input_code,
            label: getCommodityDisplayName(r.input_code),
            detail: r.mechanism_note,
          }));
        const demandProxies = subRules
          .filter((r) => r.relationship_type !== "Cost Input")
          .map((r) => ({
            code: r.input_code,
            label: getCommodityDisplayName(r.input_code),
            detail: r.mechanism_note,
          }));
        map[eq.ticker] = { costInputs, demandProxies, fromProductRollup: false };
      }
    }

    return map;
  }, [equities, tickerRollupMap, tickerDriversMap, sectorSensitivitiesMap, inputNameMap]);

  const filteredEquities = useMemo(() => {
    const q = search.toLowerCase();
    const filtered = equities.filter((eq) => {
      const activeProds = (tickerProductsMap[eq.ticker] || []).filter(
        (p) => p.status === "active"
      );
      const aiDrivers = tickerDriversMap[eq.ticker] || [];

      const matchesProductSearch = activeProds.some(
        (p) =>
          p.name.toLowerCase().includes(q) ||
          p.category.toLowerCase().includes(q) ||
          p.downstream.end_demand.toLowerCase().includes(q) ||
          p.raw_materials.some((rm) => rm.name.toLowerCase().includes(q))
      );
      const matchesDriverSearch = aiDrivers.some((d) =>
        d.commodity_name.toLowerCase().includes(q)
      );
      const matchesSearch =
        eq.ticker.toLowerCase().includes(q) ||
        eq.company_name.toLowerCase().includes(q) ||
        (eq.sub_sector || "").toLowerCase().includes(q) ||
        (eq.country || "").toLowerCase().includes(q) ||
        matchesProductSearch ||
        matchesDriverSearch;

      const matchesSector =
        sectorFilter === "ALL" || eq.sub_sector === sectorFilter;
      const matchesCountry =
        countryFilter === "ALL" || eq.country === countryFilter;
      const matchesContract =
        contractFilter === "ALL" || eq.contract_type === contractFilter;
      const qual = Number(eq.qualification_years || 0);

      // Reverse index: commodity -> products -> companies
      let matchesCommodity = true;
      if (selectedCommodity) {
        const revHit = commodityReverseIndex[selectedCommodity]?.[eq.ticker];
        if (activeProds.length > 0) {
          matchesCommodity = Boolean(revHit && revHit.products.length > 0);
        } else {
          const subRules = sectorSensitivitiesMap[eq.sub_sector || ""] || [];
          const matchesSubRule = subRules.some(
            (r) => r.input_code === selectedCommodity
          );
          const matchesAiDriver = aiDrivers.some(
            (d) => d.matched_input_code === selectedCommodity
          );
          matchesCommodity = Boolean(revHit) || matchesSubRule || matchesAiDriver;
        }
      }

      // Reverse index: product category -> peers across companies
      let matchesCategory = true;
      if (selectedProductCategory) {
        const catInfo = categoryPeerIndex[selectedProductCategory];
        matchesCategory = Boolean(catInfo && catInfo.tickers.has(eq.ticker));
      }

      return (
        matchesSearch &&
        matchesSector &&
        matchesCountry &&
        matchesContract &&
        matchesCommodity &&
        matchesCategory &&
        qual >= minQualYears
      );
    });

    return [...filtered].sort((a, b) => {
      if (sortBy === "products_desc") {
        const diff =
          (tickerRollupMap[b.ticker]?.activeProductCount ?? 0) -
          (tickerRollupMap[a.ticker]?.activeProductCount ?? 0);
        return diff !== 0
          ? diff
          : (b.market_cap_usd ?? -1) - (a.market_cap_usd ?? -1);
      }
      if (sortBy === "mcap_desc") {
        const diff = (b.market_cap_usd ?? -1) - (a.market_cap_usd ?? -1);
        return diff !== 0 ? diff : a.ticker.localeCompare(b.ticker);
      }
      if (sortBy === "mcap_asc") {
        const diff = (a.market_cap_usd ?? 1e18) - (b.market_cap_usd ?? 1e18);
        return diff !== 0 ? diff : a.ticker.localeCompare(b.ticker);
      }
      if (sortBy === "rev_desc") {
        const diff = (b.adv_mat_rev_pct ?? -1) - (a.adv_mat_rev_pct ?? -1);
        return diff !== 0 ? diff : a.ticker.localeCompare(b.ticker);
      }
      if (sortBy === "rev_asc") {
        const diff = (a.adv_mat_rev_pct ?? 999) - (b.adv_mat_rev_pct ?? 999);
        return diff !== 0 ? diff : a.ticker.localeCompare(b.ticker);
      }
      if (sortBy === "country_asc") {
        const countryDiff = (a.country || "ZZZ").localeCompare(
          b.country || "ZZZ"
        );
        if (countryDiff !== 0) return countryDiff;
        return (b.market_cap_usd ?? -1) - (a.market_cap_usd ?? -1);
      }
      if (sortBy === "moat_desc") {
        const diff =
          Number(b.qualification_years || 0) - Number(a.qualification_years || 0);
        return diff !== 0 ? diff : a.ticker.localeCompare(b.ticker);
      }
      return a.ticker.localeCompare(b.ticker);
    });
  }, [
    equities,
    search,
    sectorFilter,
    countryFilter,
    contractFilter,
    sortBy,
    selectedCommodity,
    selectedProductCategory,
    commodityReverseIndex,
    categoryPeerIndex,
    tickerProductsMap,
    tickerRollupMap,
    sectorSensitivitiesMap,
    tickerDriversMap,
    minQualYears,
  ]);

  const filteredFilings = useMemo(() => {
    if (feedThemeFilter === "ALL") return filings;
    return filings.filter((f) => (f.theme_tags || []).includes(feedThemeFilter));
  }, [filings, feedThemeFilter]);

  const selectedEquity = useMemo(
    () => equities.find((e) => e.ticker === selectedTicker) || null,
    [equities, selectedTicker]
  );

  const selectedEquityProducts = selectedTicker
    ? tickerProductsMap[selectedTicker] || []
    : [];

  const selectedEquityExcluded = selectedTicker
    ? tickerExcludedMap[selectedTicker] || []
    : [];

  const selectedEquityNote = selectedTicker
    ? tickerNoteMap[selectedTicker] || null
    : null;

  const selectedEquityFilings = selectedTicker
    ? tickerFilingsMap[selectedTicker] || []
    : [];

  const selectedEquityDrivers = selectedTicker
    ? tickerDriversMap[selectedTicker] || []
    : [];

  const selectedEquitySensitivities = selectedEquity?.sub_sector
    ? sectorSensitivitiesMap[selectedEquity.sub_sector] || []
    : [];

  const selectedEquitySupplyChain = selectedTicker
    ? tickerSupplyChainMap[selectedTicker] || []
    : [];

  const upstreamSuppliers = selectedEquitySupplyChain.filter(
    (s) => s.direction === "upstream_supplier"
  );
  const filingCompetitors = selectedEquitySupplyChain.filter(
    (s) => s.direction === "competitor"
  );
  const downstreamCustomers = selectedEquitySupplyChain.filter(
    (s) => s.direction === "downstream_customer"
  );

  const universePeers = useMemo(() => {
    if (!selectedEquity?.sub_sector) return [];
    return equities.filter(
      (e) =>
        e.sub_sector === selectedEquity.sub_sector &&
        e.ticker !== selectedEquity.ticker
    );
  }, [equities, selectedEquity]);

  // Helper: click a logged commodity chip in the dossier -> filter products -> companies
  const handleSelectCommodityFromDossier = (code: string) => {
    setSelectedCommodity(code);
    setSelectedProductCategory(null);
    setActiveTab("screener");
    setSelectedTicker(null);
  };

  // Helper: click a product category in the dossier -> filter peers across companies
  const handleSelectCategoryFromDossier = (category: string) => {
    setSelectedProductCategory(category);
    setSelectedCommodity(null);
    setActiveTab("screener");
    setSelectedTicker(null);
  };

  return (
    <div className="min-h-screen bg-[#090d16] text-slate-100 font-sans selection:bg-sky-500/30">
      {/* Top Bar */}
      <header className="border-b border-slate-800/80 bg-[#0c1220]/90 backdrop-blur sticky top-0 z-20">
        <div className="max-w-7xl mx-auto px-6 py-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <div>
            <div className="flex items-center gap-2.5">
              <span className="h-2.5 w-2.5 rounded-full bg-emerald-400 animate-pulse" />
              <h1 className="text-lg font-semibold tracking-tight text-white">
                Advanced Materials Intelligence &amp; Macro Terminal
              </h1>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              {coveredCompanyCount} of {equities.length} Companies Covered • {filings.length} AI-Parsed Filings &amp; Profiles • Product &amp; Commodity Sensitivity Engine
            </p>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => setActiveTab("screener")}
              className={`px-3.5 py-1.5 rounded-lg text-xs font-medium transition ${
                activeTab === "screener"
                  ? "bg-sky-500 text-white"
                  : "bg-slate-800/80 text-slate-300 hover:bg-slate-800"
              }`}
            >
              Universe &amp; Commodity Matrix
            </button>
            <button
              onClick={() => setActiveTab("feed")}
              className={`px-3.5 py-1.5 rounded-lg text-xs font-medium transition flex items-center gap-1.5 ${
                activeTab === "feed"
                  ? "bg-sky-500 text-white"
                  : "bg-slate-800/80 text-slate-300 hover:bg-slate-800"
              }`}
            >
              <span>Earnings &amp; 10-K Feed</span>
              <span className="px-1.5 py-0.2 rounded bg-slate-900/60 text-[10px] font-mono">
                {filings.length}
              </span>
            </button>
            <button
              onClick={toggleTheme}
              className="px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800/80 text-slate-300 hover:bg-slate-800 border border-slate-700/80 transition"
              title="Toggle Light / Dark Mode"
            >
              {isLight ? "🌙 Dark" : "☀️ Light"}
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-8 space-y-8">
        {/* AI Macro & Thematic Narrative Synthesis */}
        <section className="rounded-2xl bg-gradient-to-br from-[#11192c] via-[#0f172a] to-[#0b1120] border border-sky-500/25 p-6 shadow-xl">
          <div className="flex flex-col md:flex-row md:items-center justify-between gap-3 pb-4 border-b border-slate-800/80">
            <div>
              <div className="flex items-center gap-2">
                <span className="px-2 py-0.5 rounded text-[10px] font-mono uppercase tracking-wider bg-sky-500/15 text-sky-400 border border-sky-500/30">
                  AI Cross-Filing Synthesis
                </span>
                {narrative && (
                  <span className="text-xs font-mono text-emerald-400">
                    Regime: {narrative.overall_regime}
                  </span>
                )}
              </div>
              <h2 className="text-base font-semibold text-white mt-1.5">
                Cross-Market Narrative: Filings, Commodities &amp; Secular Capex
              </h2>
            </div>

            <div className="flex flex-wrap gap-1.5">
              {[
                { id: "executive", label: "Overall Market" },
                { id: "ai", label: "AI & Semis" },
                { id: "aero", label: "Aerospace & Defense" },
                { id: "commodities", label: "Commodities & Margins" },
                { id: "ev", label: "Electrification & EVs" },
              ].map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setNarrativeTab(tab.id as typeof narrativeTab)}
                  className={`px-3 py-1 rounded-md text-xs font-medium transition ${
                    narrativeTab === tab.id
                      ? "bg-sky-500/20 text-sky-300 border border-sky-500/40"
                      : "bg-slate-900/60 text-slate-400 border border-slate-800 hover:text-slate-200"
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          </div>

          <div className="pt-4 text-sm leading-relaxed text-slate-200">
            {!narrative ? (
              <p className="text-slate-400 italic">
                Run the GitHub Actions workflow (`filings_ai.py`) to generate the cross-filing macro narrative.
              </p>
            ) : (
              <>
                {narrativeTab === "executive" && <p>{narrative.executive_brief}</p>}
                {narrativeTab === "ai" && <p>{narrative.ai_theme_summary}</p>}
                {narrativeTab === "aero" && <p>{narrative.aerospace_theme_summary}</p>}
                {narrativeTab === "commodities" && <p>{narrative.commodities_theme_summary}</p>}
                {narrativeTab === "ev" && <p>{narrative.electrification_battery_summary}</p>}
                <div className="mt-3 text-[11px] font-mono text-slate-500">
                  Synthesized across {coveredCompanyCount} covered companies ({filings.length} filings) &amp; {inputs.length} commodity feeds • Updated {narrative.generated_date}
                </div>
              </>
            )}
          </div>
        </section>

        {/* Interactive Commodity & Macro Filter Cards */}
        <section>
          <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
            <div className="flex items-center gap-3 flex-wrap">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                Click Any Commodity or Macro Driver to Filter Products &amp; Exposed Stocks
              </h2>
              {selectedCommodity && (
                <button
                  onClick={() => setSelectedCommodity(null)}
                  className="text-xs font-mono text-sky-400 hover:text-sky-300 underline"
                >
                  Clear Commodity ({getCommodityDisplayName(selectedCommodity)})
                </button>
              )}
              {selectedProductCategory && (
                <button
                  onClick={() => setSelectedProductCategory(null)}
                  className="text-xs font-mono text-purple-400 hover:text-purple-300 underline"
                >
                  Clear Product Category ({selectedProductCategory})
                </button>
              )}
            </div>
            <span className="text-xs text-slate-500">EOD Market, FRED &amp; AI Spot</span>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-4 gap-3">
            {inputs.map((item) => {
              const isSelected = selectedCommodity === item.input_code;
              const linkedCompanyCount = Object.keys(
                commodityReverseIndex[item.input_code] || {}
              ).length;
              return (
                <button
                  key={item.input_code}
                  onClick={() =>
                    setSelectedCommodity(isSelected ? null : item.input_code)
                  }
                  title={item.source_note || undefined}
                  className={`text-left rounded-xl p-3.5 border transition ${
                    isSelected
                      ? "bg-sky-950/50 border-sky-400 ring-1 ring-sky-400/50"
                      : "bg-[#111827]/90 border-slate-800/90 hover:border-slate-700"
                  }`}
                >
                  <div className="flex items-center justify-between gap-1.5">
                    <span className="text-[11px] font-medium text-sky-400 truncate">
                      {item.category || "Macro Input"}
                    </span>
                    <div className="flex items-center gap-1 shrink-0">
                      {linkedCompanyCount > 0 && (
                        <span className="px-1.5 py-0.2 rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 text-[9px] font-mono">
                          {linkedCompanyCount} Prod. Co.
                        </span>
                      )}
                      {item.is_proxy && (
                        <span className="px-1.5 py-0.2 rounded bg-amber-500/15 text-amber-300 border border-amber-500/30 text-[9px] font-mono uppercase">
                          AI Spot
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="mt-1 text-xs font-semibold text-white line-clamp-1">
                    {item.input_name}
                  </div>
                  <div className="mt-2.5 flex items-baseline justify-between gap-1">
                    <span className="text-lg font-semibold font-mono text-white">
                      {Number(item.value).toLocaleString(undefined, {
                        minimumFractionDigits: 2,
                        maximumFractionDigits: 2,
                      })}
                    </span>
                    <span className="text-[10px] text-slate-500 truncate">
                      {item.unit}
                    </span>
                  </div>
                </button>
              );
            })}
          </div>
        </section>

        {/* TAB 1: Equity Universe & Product/Commodity Exposure Table */}
        {activeTab === "screener" && (
          <section className="space-y-4">
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-6 gap-3 bg-[#111827]/80 border border-slate-800 rounded-xl p-4">
              <div className="sm:col-span-2 lg:col-span-2">
                <label className="block text-[11px] font-mono uppercase text-slate-400 mb-1.5">
                  Search Ticker, Company, Product, Commodity, or Country
                </label>
                <input
                  type="text"
                  placeholder="Search e.g. 'CMP', 'Cobalt', 'OLED', 'MRK.DE'..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="w-full rounded-lg bg-[#090d16] border border-slate-700/80 px-3.5 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-sky-500"
                />
              </div>

              <div>
                <label className="block text-[11px] font-mono uppercase text-slate-400 mb-1.5">
                  Country
                </label>
                <select
                  value={countryFilter}
                  onChange={(e) => setCountryFilter(e.target.value)}
                  className="w-full rounded-lg bg-[#090d16] border border-slate-700/80 px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-sky-500"
                >
                  {countries.map((c) => (
                    <option key={c} value={c}>
                      {c === "ALL" ? "All Countries" : c}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-[11px] font-mono uppercase text-slate-400 mb-1.5">
                  Sub-Sector
                </label>
                <select
                  value={sectorFilter}
                  onChange={(e) => setSectorFilter(e.target.value)}
                  className="w-full rounded-lg bg-[#090d16] border border-slate-700/80 px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-sky-500"
                >
                  {sectors.map((sec) => (
                    <option key={sec} value={sec}>
                      {sec === "ALL" ? "All Sub-Sectors" : sec}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-[11px] font-mono uppercase text-slate-400 mb-1.5">
                  Est. Pricing Model
                </label>
                <select
                  value={contractFilter}
                  onChange={(e) => setContractFilter(e.target.value)}
                  className="w-full rounded-lg bg-[#090d16] border border-slate-700/80 px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-sky-500"
                >
                  <option value="ALL">All Contracts</option>
                  <option value="take_or_pay">Take-or-Pay</option>
                  <option value="pass_through">Pass-Through</option>
                  <option value="fixed_spread">Fixed Spread</option>
                </select>
              </div>

              <div>
                <label className="block text-[11px] font-mono uppercase text-slate-400 mb-1.5">
                  Sort By
                </label>
                <select
                  value={sortBy}
                  onChange={(e) => setSortBy(e.target.value as SortOption)}
                  className="w-full rounded-lg bg-[#090d16] border border-slate-700/80 px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-sky-500"
                >
                  <option value="mcap_desc">Market Cap USD (High → Low)</option>
                  <option value="mcap_asc">Market Cap USD (Low → High)</option>
                  <option value="products_desc">Active Products (High → Low)</option>
                  <option value="rev_desc">Adv. Mat. Rev % (High → Low)</option>
                  <option value="rev_asc">Adv. Mat. Rev % (Low → High)</option>
                  <option value="country_asc">Country (A → Z)</option>
                  <option value="moat_desc">Est. Moat (High → Low)</option>
                  <option value="ticker_asc">Ticker (A → Z)</option>
                </select>
              </div>

              <div className="sm:col-span-2 lg:col-span-6 flex items-center gap-4 pt-1 border-t border-slate-800/70">
                <label className="text-[11px] font-mono uppercase text-slate-400 whitespace-nowrap">
                  Min Est. Moat: {minQualYears} yrs
                </label>
                <input
                  type="range"
                  min={0}
                  max={5}
                  step={0.5}
                  value={minQualYears}
                  onChange={(e) => setMinQualYears(Number(e.target.value))}
                  className="w-48 accent-sky-400 cursor-pointer"
                />
                {(countryFilter !== "ALL" ||
                  sectorFilter !== "ALL" ||
                  contractFilter !== "ALL" ||
                  minQualYears > 0 ||
                  selectedCommodity ||
                  selectedProductCategory ||
                  search) && (
                  <button
                    onClick={() => {
                      setSearch("");
                      setCountryFilter("ALL");
                      setSectorFilter("ALL");
                      setContractFilter("ALL");
                      setMinQualYears(0);
                      setSelectedCommodity(null);
                      setSelectedProductCategory(null);
                    }}
                    className="ml-auto text-xs font-mono text-sky-400 hover:text-sky-300 underline"
                  >
                    Reset All Filters
                  </button>
                )}
              </div>
            </div>

            {/* Active Reverse-Index Banner: Commodity -> Products -> Companies or Category -> Peers */}
            {(selectedCommodity || selectedProductCategory) && (
              <div className="rounded-xl border border-sky-500/35 bg-sky-950/25 px-5 py-3.5 flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs">
                <div>
                  {selectedCommodity && (
                    <div className="space-y-1">
                      <div className="font-semibold text-sky-300">
                        Reverse Index: {getCommodityDisplayName(selectedCommodity)} &rarr; Matching Products &rarr; Companies
                      </div>
                      <div className="text-slate-300">
                        {Object.keys(commodityReverseIndex[selectedCommodity] || {}).length > 0
                          ? `Matched across ${
                              Object.values(commodityReverseIndex[selectedCommodity] || {}).reduce(
                                (acc, h) => acc + h.products.length,
                                0
                              )
                            } active product(s) in ${
                              Object.keys(commodityReverseIndex[selectedCommodity] || {}).length
                            } decomposed company/companies (plus legacy sub-sector matches below).`
                          : "Filtered via sub-sector & filing commodity rules (no decomposed products linked yet)."}
                      </div>
                    </div>
                  )}
                  {selectedProductCategory && (
                    <div className="space-y-1">
                      <div className="font-semibold text-purple-300">
                        Product Category Peers: {selectedProductCategory}
                      </div>
                      <div className="text-slate-300">
                        Showing all companies with active products in{" "}
                        <span className="text-white font-medium">{selectedProductCategory}</span>{" "}
                        and their mapped peer tickers.
                      </div>
                    </div>
                  )}
                </div>
                <button
                  onClick={() => {
                    setSelectedCommodity(null);
                    setSelectedProductCategory(null);
                  }}
                  className="px-3 py-1 rounded bg-slate-800 hover:bg-slate-700 text-xs font-mono text-slate-200 shrink-0"
                >
                  Clear Reverse Filter ✕
                </button>
              </div>
            )}

            <div className="rounded-xl border border-slate-800 bg-[#111827]/70 overflow-hidden">
              <div className="px-5 py-3 border-b border-slate-800 flex items-center justify-between text-xs text-slate-400">
                <span>
                  Showing <strong className="text-white">{filteredEquities.length}</strong> of{" "}
                  {equities.length} companies
                </span>
                <span className="text-sky-400 font-medium">
                  Click any company row for Products, Value-Chain &amp; Filing Dossier &rarr;
                </span>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="border-b border-slate-800/90 text-[11px] font-mono uppercase text-slate-400 bg-[#0c1220]">
                      <th className="py-3.5 px-4">Ticker</th>
                      <th className="py-3.5 px-4">Company</th>
                      <th className="py-3.5 px-4">Country</th>
                      <th className="py-3.5 px-4 text-right">Market Cap</th>
                      <th className="py-3.5 px-4 text-center">Products</th>
                      <th
                        className="py-3.5 px-4"
                        title="Raw materials -> margin (lagged), rolled up from active products"
                      >
                        Cost Inputs (Margin)
                      </th>
                      <th
                        className="py-3.5 px-4"
                        title="Downstream end-demand -> volume, rolled up from active products"
                      >
                        Demand Proxies (Volume)
                      </th>
                      <th
                        className="py-3.5 px-4"
                        title="Analyst-classified contract pricing structure"
                      >
                        Est. Pricing
                      </th>
                      <th className="py-3.5 px-4">AI Filings</th>
                      <th
                        className="py-3.5 px-4 text-right"
                        title="Analyst-estimated customer qualification timeline"
                      >
                        Est. Moat
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/60 text-sm">
                    {filteredEquities.map((eq) => {
                      const badge =
                        CONTRACT_BADGES[eq.contract_type || ""] || {
                          label: eq.contract_type || "Standard",
                          style: "bg-slate-800 text-slate-300 border-slate-700",
                        };
                      const qual = Number(eq.qualification_years || 0);
                      const rollup = tickerRollupMap[eq.ticker];
                      const roleDrivers = companyDriversByRole[eq.ticker] || {
                        costInputs: [],
                        demandProxies: [],
                        fromProductRollup: false,
                      };
                      const eqFilings = tickerFilingsMap[eq.ticker] || [];
                      const matchedCommodityProducts = selectedCommodity
                        ? commodityReverseIndex[selectedCommodity]?.[eq.ticker]?.products || []
                        : [];

                      return (
                        <tr
                          key={eq.ticker}
                          onClick={() => setSelectedTicker(eq.ticker)}
                          className="hover:bg-slate-800/60 cursor-pointer transition-colors"
                        >
                          <td className="py-3.5 px-4 font-mono font-semibold text-sky-400">
                            {eq.ticker}
                          </td>
                          <td className="py-3.5 px-4">
                            <div className="font-medium text-white">{eq.company_name}</div>
                            <div className="text-xs text-slate-400">{eq.sub_sector}</div>
                            {matchedCommodityProducts.length > 0 && (
                              <div className="mt-1 flex flex-wrap gap-1">
                                {matchedCommodityProducts.map((mp) => (
                                  <span
                                    key={mp.id}
                                    className="px-1.5 py-0.2 rounded bg-sky-500/20 text-sky-200 border border-sky-400/40 text-[10px] font-mono"
                                  >
                                    Match: {mp.name}
                                  </span>
                                ))}
                              </div>
                            )}
                          </td>
                          <td className="py-3.5 px-4 text-xs text-slate-300 whitespace-nowrap">
                            {eq.country || "—"}
                          </td>
                          <td className="py-3.5 px-4 text-right font-mono text-xs text-slate-200 whitespace-nowrap">
                            {formatMarketCap(eq.market_cap_usd)}
                          </td>
                          <td className="py-3.5 px-4 text-center font-mono">
                            {rollup && rollup.activeProductCount > 0 ? (
                              <span
                                className="inline-flex items-center px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 text-xs font-semibold"
                                title={
                                  rollup.unloggedMaterials.length > 0
                                    ? `${rollup.activeProductCount} active products (${rollup.unloggedMaterials.length} unlogged raw materials)`
                                    : `${rollup.activeProductCount} active products`
                                }
                              >
                                {rollup.activeProductCount}
                              </span>
                            ) : (
                              <span className="text-xs text-slate-600">—</span>
                            )}
                          </td>

                          {/* Cost Inputs (Raw Materials -> Margin) */}
                          <td className="py-3.5 px-4">
                            <div className="flex flex-wrap gap-1.5 max-w-[210px]">
                              {roleDrivers.costInputs.length === 0 ? (
                                rollup?.unloggedMaterials.length ? (
                                  <span
                                    className="px-2 py-0.5 rounded text-[10px] font-mono border border-dashed border-slate-600 text-slate-400"
                                    title={`Unlogged raw materials: ${rollup.unloggedMaterials.join(", ")}`}
                                  >
                                    {rollup.unloggedMaterials.length} unlogged
                                  </span>
                                ) : (
                                  <span className="text-xs text-slate-500">—</span>
                                )
                              ) : (
                                <>
                                  {roleDrivers.costInputs.slice(0, 3).map((ci, idx) => (
                                    <span
                                      key={`${ci.label}-${idx}`}
                                      className="px-2 py-0.5 rounded text-[11px] font-medium border bg-rose-500/10 text-rose-300 border-rose-500/30"
                                      title={
                                        ci.detail ||
                                        (roleDrivers.fromProductRollup
                                          ? "Rolled up from active products (cost input -> margin)"
                                          : undefined)
                                      }
                                    >
                                      {ci.label}
                                    </span>
                                  ))}
                                  {roleDrivers.costInputs.length > 3 && (
                                    <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-slate-800 text-slate-400 border border-slate-700">
                                      +{roleDrivers.costInputs.length - 3}
                                    </span>
                                  )}
                                  {rollup && rollup.unloggedMaterials.length > 0 && (
                                    <span
                                      className="px-1.5 py-0.5 rounded text-[10px] font-mono border border-dashed border-slate-600 text-slate-400"
                                      title={`Unlogged raw materials: ${rollup.unloggedMaterials.join(", ")}`}
                                    >
                                      +{rollup.unloggedMaterials.length} unlogged
                                    </span>
                                  )}
                                </>
                              )}
                            </div>
                          </td>

                          {/* Demand Proxies (Downstream -> Volume) */}
                          <td className="py-3.5 px-4">
                            <div className="flex flex-wrap gap-1.5 max-w-[210px]">
                              {roleDrivers.demandProxies.length === 0 ? (
                                <span className="text-xs text-slate-500">—</span>
                              ) : (
                                <>
                                  {roleDrivers.demandProxies.slice(0, 3).map((dp, idx) => (
                                    <span
                                      key={`${dp.label}-${idx}`}
                                      className="px-2 py-0.5 rounded text-[11px] font-medium border bg-emerald-500/10 text-emerald-300 border-emerald-500/30"
                                      title={
                                        dp.detail ||
                                        (roleDrivers.fromProductRollup
                                          ? "Rolled up from active products (downstream -> volume)"
                                          : undefined)
                                      }
                                    >
                                      {dp.label}
                                    </span>
                                  ))}
                                  {roleDrivers.demandProxies.length > 3 && (
                                    <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-slate-800 text-slate-400 border border-slate-700">
                                      +{roleDrivers.demandProxies.length - 3}
                                    </span>
                                  )}
                                </>
                              )}
                            </div>
                          </td>

                          <td className="py-3.5 px-4">
                            <span
                              className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border ${badge.style}`}
                            >
                              {badge.label}
                            </span>
                          </td>
                          <td className="py-3.5 px-4">
                            {eqFilings.length > 0 ? (
                              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-purple-500/15 text-purple-300 border border-purple-500/30 text-xs font-mono">
                                {eqFilings.length} Brief{eqFilings.length > 1 ? "s" : ""}
                              </span>
                            ) : (
                              <span className="text-xs text-slate-600 font-mono">Queued</span>
                            )}
                          </td>
                          <td className="py-3.5 px-4 text-right font-mono text-slate-200">
                            {qual.toFixed(1)}y
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        )}

        {/* TAB 2: Live Earnings & SEC 10-K/10-Q Intelligence Feed */}
        {activeTab === "feed" && (
          <section className="space-y-4">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
              <h2 className="text-sm font-semibold text-white">
                Latest AI-Parsed 10-K, 10-Q &amp; Global Company Profiles
              </h2>
              <span className="text-xs text-slate-400">
                Showing {filteredFilings.length} of {filings.length} briefs across {coveredCompanyCount} companies • Click any card for full dossier
              </span>
            </div>

            <div className="flex flex-wrap gap-1.5">
              {feedThemes.map((theme) => (
                <button
                  key={theme}
                  onClick={() => setFeedThemeFilter(theme)}
                  className={`px-3 py-1 rounded-full text-xs font-medium border transition ${
                    feedThemeFilter === theme
                      ? "bg-sky-500 text-white border-sky-400"
                      : "bg-[#111827] text-slate-400 border-slate-800 hover:text-slate-200"
                  }`}
                >
                  {theme === "ALL" ? "All Themes" : theme}
                </button>
              ))}
            </div>

            {filteredFilings.length === 0 ? (
              <div className="rounded-xl border border-slate-800 bg-[#111827]/70 p-8 text-center text-sm text-slate-400">
                {filings.length === 0
                  ? "No filings parsed yet. Trigger the GitHub Actions workflow (`filings_ai.py`) to populate SEC 10-K/10-Q and earnings briefs."
                  : `No filings tagged with "${feedThemeFilter}" yet.`}
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {filteredFilings.map((f) => (
                  <div
                    key={f.id}
                    onClick={() => setSelectedTicker(f.ticker)}
                    className="rounded-xl border border-slate-800 bg-[#111827]/90 p-5 hover:border-sky-500/40 cursor-pointer transition space-y-3"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="font-mono font-bold text-sky-400 text-sm shrink-0">
                          {f.ticker}
                        </span>
                        <span className="text-xs font-medium text-slate-300 truncate">
                          {f.company_name}
                        </span>
                      </div>
                      <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-300 text-[11px] font-mono shrink-0">
                        {f.filing_type === "Earnings Brief"
                          ? `Profile & News • ${f.filing_date}`
                          : `${f.filing_type} • ${f.filing_date}`}
                      </span>
                    </div>

                    <h3 className="text-sm font-semibold text-white leading-snug">
                      {f.headline}
                    </h3>

                    <p className="text-xs text-slate-300 leading-relaxed">
                      {f.executive_summary}
                    </p>

                    {f.theme_tags && f.theme_tags.length > 0 && (
                      <div className="flex flex-wrap gap-1.5 pt-1">
                        {f.theme_tags.map((tag) => (
                          <span
                            key={tag}
                            className="px-2 py-0.5 rounded-full bg-sky-500/10 text-sky-300 border border-sky-500/25 text-[10px] font-medium"
                          >
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </section>
        )}
      </main>

      {/* Slide-Over Company Deep-Dive Dossier Drawer */}
      {selectedEquity && (
        <div className="fixed inset-0 z-50 flex justify-end bg-black/70 backdrop-blur-sm">
          <div className="w-full max-w-3xl bg-[#0c1220] border-l border-slate-800 h-full overflow-y-auto p-6 space-y-6 shadow-2xl">
            <div className="flex items-start justify-between border-b border-slate-800 pb-4">
              <div>
                <div className="flex items-center gap-2.5 flex-wrap">
                  <span className="text-xl font-mono font-bold text-sky-400">
                    {selectedEquity.ticker}
                  </span>
                  <span className="px-2.5 py-0.5 rounded-full text-xs bg-slate-800 text-slate-300">
                    {selectedEquity.sub_sector}
                  </span>
                  {selectedEquity.country && (
                    <span className="px-2.5 py-0.5 rounded-full text-xs bg-slate-800/80 text-slate-300 border border-slate-700">
                      {selectedEquity.country}
                    </span>
                  )}
                </div>
                <h2 className="text-lg font-semibold text-white mt-1">
                  {selectedEquity.company_name}
                </h2>
                <div className="flex flex-wrap items-center gap-3 mt-2 text-xs font-mono text-slate-400">
                  {selectedEquity.market_cap_usd != null && (
                    <>
                      <span className="text-white font-semibold">
                        Mkt Cap: {formatMarketCap(selectedEquity.market_cap_usd)}
                      </span>
                      <span>•</span>
                    </>
                  )}
                  <span>Est. Pricing: {selectedEquity.contract_type || "Standard"}</span>
                  <span>•</span>
                  <span>Est. Moat: {selectedEquity.qualification_years} yrs</span>
                  {selectedEquity.adv_mat_rev_pct != null && (
                    <>
                      <span>•</span>
                      <span className="text-emerald-400">
                        Adv. Mat. Rev: {selectedEquity.adv_mat_rev_pct}%
                      </span>
                    </>
                  )}
                </div>
              </div>
              <button
                onClick={() => setSelectedTicker(null)}
                className="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs text-slate-200"
              >
                Close ✕
              </button>
            </div>

            {/* PRODUCTS TABLE: [Raw Material] -> [Product] -> [Downstream] */}
            <div className="space-y-3">
              <div className="flex items-center justify-between flex-wrap gap-2">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                  Advanced Materials Products ({selectedEquityProducts.filter((p) => p.status === "active").length} Active)
                </h3>
                <span className="text-[10px] font-mono text-slate-400">
                  Click solid raw material to filter companies • Click category for peers
                </span>
              </div>

              {selectedEquityNote && (
                <div className="rounded-lg bg-slate-900/90 border border-slate-800 px-3.5 py-2 text-xs text-slate-300">
                  {selectedEquityNote}
                </div>
              )}

              {selectedEquityProducts.length === 0 ? (
                <p className="text-xs text-slate-500">
                  Product-level decomposition not yet populated for this ticker.
                </p>
              ) : (
                <div className="rounded-xl border border-slate-800 bg-[#111827] overflow-hidden">
                  <div className="overflow-x-auto">
                    <table className="w-full text-left border-collapse text-xs">
                      <thead>
                        <tr className="border-b border-slate-800 bg-[#090d16] text-[10px] font-mono uppercase text-slate-400">
                          <th className="py-2.5 px-3.5">Value-Chain Flow ([Raw Material] &rarr; [Product] &rarr; [Downstream])</th>
                          <th className="py-2.5 px-3.5">Category &amp; Specs</th>
                          <th className="py-2.5 px-3.5 text-right">Status / Conf.</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-800/70">
                        {selectedEquityProducts.map((prod) => {
                          const proxyCode = prod.downstream?.demand_proxy_ref;
                          const isProxyValid = Boolean(
                            proxyCode && validRegistryCodes.has(proxyCode)
                          );

                          return (
                            <tr
                              key={prod.id}
                              className={
                                prod.status === "divested"
                                  ? "opacity-55 bg-slate-950/50"
                                  : "hover:bg-slate-800/40"
                              }
                            >
                              {/* Chain Chips: [raw material] -> [product] -> [downstream (emphasized accent fill)] */}
                              <td className="py-3 px-3.5 align-top space-y-2">
                                <div className="flex flex-wrap items-center gap-1.5">
                                  {/* Raw Material Chips */}
                                  <div className="flex flex-wrap items-center gap-1">
                                    {(prod.raw_materials || []).map((rm, rIdx) => {
                                      const isLogged = Boolean(
                                        rm.commodity_ref &&
                                          validRegistryCodes.has(rm.commodity_ref)
                                      );
                                      if (isLogged && rm.commodity_ref) {
                                        return (
                                          <button
                                            key={`${rm.name}-${rIdx}`}
                                            type="button"
                                            onClick={() =>
                                              handleSelectCommodityFromDossier(
                                                rm.commodity_ref!
                                              )
                                            }
                                            title={`Logged registry commodity: ${getCommodityDisplayName(
                                              rm.commodity_ref
                                            )} (${rm.role} • ${rm.basis}). Click to reverse-filter companies.`}
                                            className="px-2 py-0.5 rounded bg-amber-500/20 hover:bg-amber-500/30 text-amber-200 border border-solid border-amber-400/60 font-medium transition cursor-pointer"
                                          >
                                            {rm.name}
                                          </button>
                                        );
                                      }
                                      return (
                                        <span
                                          key={`${rm.name}-${rIdx}`}
                                          title={`Unlogged raw material (${rm.role} • ${rm.basis}) — not yet in commodity registry`}
                                          className="px-2 py-0.5 rounded bg-slate-900/70 text-slate-400 border border-dashed border-slate-600 inline-flex items-center gap-1"
                                        >
                                          <span>{rm.name}</span>
                                          <span className="text-[9px] font-mono uppercase text-slate-500">
                                            unlogged
                                          </span>
                                        </span>
                                      );
                                    })}
                                  </div>

                                  <span className="text-slate-500 font-mono">&rarr;</span>

                                  {/* Company Product Chip */}
                                  <span className="px-2.5 py-0.5 rounded bg-slate-800 text-white border border-slate-600 font-semibold">
                                    {prod.name}
                                  </span>

                                  <span className="text-slate-500 font-mono">&rarr;</span>

                                  {/* Downstream Chip (Emphasized Accent Fill) */}
                                  {isProxyValid && proxyCode ? (
                                    <button
                                      type="button"
                                      onClick={() =>
                                        handleSelectCommodityFromDossier(proxyCode)
                                      }
                                      title={`Downstream demand proxy: ${getCommodityDisplayName(
                                        proxyCode
                                      )} (${prod.downstream.process_step} -> ${
                                        prod.downstream.end_product
                                      }). Click to reverse-filter companies.`}
                                      className="px-2.5 py-0.5 rounded bg-sky-500 text-slate-950 font-semibold shadow-sm shadow-sky-500/30 hover:bg-sky-400 transition cursor-pointer"
                                    >
                                      {prod.downstream.end_demand}
                                    </button>
                                  ) : (
                                    <span
                                      title={`${prod.downstream.process_step} -> ${prod.downstream.end_product}`}
                                      className="px-2.5 py-0.5 rounded bg-sky-500 text-slate-950 font-semibold shadow-sm shadow-sky-500/30"
                                    >
                                      {prod.downstream.end_demand}
                                    </span>
                                  )}
                                </div>

                                <div className="text-[11px] text-slate-400">
                                  <span className="text-slate-500">Process:</span>{" "}
                                  {prod.downstream.process_step} &bull;{" "}
                                  <span className="text-slate-500">End Product:</span>{" "}
                                  {prod.downstream.end_product}
                                </div>

                                {prod.peers && prod.peers.length > 0 && (
                                  <div className="flex flex-wrap items-center gap-1 pt-0.5">
                                    <span className="text-[10px] font-mono text-slate-500">
                                      Product Peers:
                                    </span>
                                    {prod.peers.map((peerTk) => (
                                      <button
                                        key={peerTk}
                                        type="button"
                                        onClick={() => setSelectedTicker(peerTk)}
                                        className="px-1.5 py-0.2 rounded bg-[#090d16] hover:bg-slate-800 text-sky-400 border border-slate-700 font-mono text-[10px]"
                                      >
                                        {peerTk}
                                      </button>
                                    ))}
                                  </div>
                                )}
                              </td>

                              {/* Clickable Category + Advanced Qualification Specs */}
                              <td className="py-3 px-3.5 align-top space-y-1.5 max-w-[220px]">
                                <button
                                  type="button"
                                  onClick={() =>
                                    handleSelectCategoryFromDossier(prod.category)
                                  }
                                  title="Click to view all peer companies in this product category"
                                  className="px-2 py-0.5 rounded bg-purple-500/15 hover:bg-purple-500/25 text-purple-300 border border-purple-500/35 font-medium text-left transition cursor-pointer"
                                >
                                  {prod.category}
                                </button>
                                <div className="text-[10px] font-mono text-slate-400">
                                  BU: {prod.business_unit}
                                </div>
                                <div className="text-[11px] text-slate-300 leading-snug">
                                  <span className="text-slate-500">Spec:</span>{" "}
                                  {prod.advanced.performance_spec}
                                </div>
                                <div className="text-[10px] text-slate-400">
                                  <span className="text-slate-500">Barrier:</span>{" "}
                                  {prod.advanced.switching_barrier}
                                </div>
                              </td>

                              {/* Status, As-Of, Confidence & Source */}
                              <td className="py-3 px-3.5 align-top text-right font-mono space-y-1 whitespace-nowrap">
                                <div>
                                  <span
                                    className={`px-1.5 py-0.5 rounded text-[10px] uppercase ${
                                      prod.status === "active"
                                        ? "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30"
                                        : "bg-rose-500/15 text-rose-300 border border-rose-500/30"
                                    }`}
                                  >
                                    {prod.status}
                                  </span>
                                </div>
                                <div className="text-[10px] text-slate-400">
                                  {prod.as_of}
                                </div>
                                <div className="text-[10px] text-slate-500">
                                  conf: {prod.conf} ({prod.src.type})
                                </div>
                                {prod.pricing_structure && (
                                  <div className="text-[10px] text-sky-300">
                                    {prod.pricing_structure}
                                  </div>
                                )}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Excluded Non-Materials / Divested Items */}
              {selectedEquityExcluded.length > 0 && (
                <div className="rounded-lg bg-[#111827]/70 border border-slate-800/80 p-3 space-y-1.5">
                  <div className="text-[10px] font-mono uppercase text-slate-400">
                    Excluded Segments / Non-Material Lines ({selectedEquityExcluded.length})
                  </div>
                  <div className="space-y-1">
                    {selectedEquityExcluded.map((ex, i) => (
                      <div key={`${ex.name}-${i}`} className="text-xs text-slate-400">
                        <span className="text-slate-300 font-medium">{ex.name}:</span>{" "}
                        {ex.reason}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* In-Depth AI Company-Specific Commodity & Input Breakdown */}
            <div className="space-y-2.5">
              <div className="flex items-center justify-between">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                  {selectedEquityDrivers.length > 0
                    ? `AI-Extracted Commodity & Input Exposure (${selectedEquityDrivers.length})`
                    : "Sub-Sector Commodity & Macro Exposure"}
                </h3>
                {selectedEquityDrivers.length > 0 && (
                  <span className="text-[10px] font-mono text-sky-400">
                    Company-Specific Filing Analysis
                  </span>
                )}
              </div>

              {selectedEquityDrivers.length > 0 ? (
                <div className="space-y-2.5">
                  {selectedEquityDrivers.map((d, idx) => (
                    <div
                      key={`${d.commodity_name}-${idx}`}
                      className="rounded-lg bg-[#111827] border border-slate-800 p-3.5 space-y-1.5"
                    >
                      <div className="flex items-center justify-between gap-2 flex-wrap">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-semibold text-white text-xs">
                            {d.commodity_name}
                          </span>
                          <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-300 text-[10px] font-mono">
                            {d.exposure_role}
                          </span>
                          {d.cogs_or_rev_share_pct != null && (
                            <span className="px-2 py-0.5 rounded bg-amber-500/15 text-amber-300 border border-amber-500/30 text-[10px] font-mono">
                              {d.cogs_or_rev_share_pct}% Share
                            </span>
                          )}
                        </div>
                        <div className="flex items-center gap-1.5">
                          {d.pass_through_lag && (
                            <span className="text-[10px] font-mono text-slate-400">
                              {d.pass_through_lag}
                            </span>
                          )}
                          <span
                            className={`text-[11px] font-mono px-2 py-0.5 rounded ${
                              d.impact_direction.includes("Inverse")
                                ? "bg-rose-500/15 text-rose-300"
                                : d.impact_direction.includes("Direct")
                                ? "bg-emerald-500/15 text-emerald-300"
                                : "bg-sky-500/15 text-sky-300"
                            }`}
                          >
                            {d.impact_direction}
                          </span>
                        </div>
                      </div>
                      <p className="text-xs text-slate-300 leading-relaxed">
                        {d.mechanism_detail}
                      </p>
                      {d.source_quote && (
                        <p className="text-[11px] text-slate-400 italic leading-snug border-l-2 border-slate-700 pl-2">
                          &ldquo;{d.source_quote}&rdquo;
                          {d.source_filing_type && (
                            <span className="not-italic font-mono text-[10px] text-slate-500 ml-1">
                              ({d.source_filing_type} {d.source_filing_date})
                            </span>
                          )}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              ) : selectedEquitySensitivities.length === 0 ? (
                <p className="text-xs text-slate-500">
                  No direct commodity rules mapped for this sub-sector yet.
                </p>
              ) : (
                <div className="space-y-2">
                  {selectedEquitySensitivities.map((s) => (
                    <div
                      key={s.input_code}
                      className="rounded-lg bg-[#111827] border border-slate-800 p-3.5"
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-semibold text-sky-400 text-xs">
                          {getCommodityDisplayName(s.input_code)} — {s.relationship_type}
                        </span>
                        <span
                          className={`text-[11px] font-mono px-2 py-0.5 rounded ${
                            s.impact_direction.includes("Inverse")
                              ? "bg-rose-500/15 text-rose-300"
                              : s.impact_direction.includes("Direct")
                              ? "bg-emerald-500/15 text-emerald-300"
                              : "bg-sky-500/15 text-sky-300"
                          }`}
                        >
                          {s.impact_direction}
                        </span>
                      </div>
                      <p className="text-xs text-slate-300 mt-1.5 leading-relaxed">
                        {s.mechanism_note}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Value-Chain & Competitive Landscape: 1. Upstream -> 2. Competitors -> 3. Downstream */}
            <div className="space-y-3">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                Value-Chain &amp; Competitive Landscape (Upstream • Competitors • Downstream)
              </h3>

              {/* 1. Upstream Suppliers */}
              <div className="rounded-lg bg-[#111827] border border-slate-800 p-3.5 space-y-2.5">
                <div className="text-[11px] font-mono uppercase tracking-wider text-amber-400">
                  1. Upstream Raw Material Suppliers ({upstreamSuppliers.length})
                </div>
                {upstreamSuppliers.length === 0 ? (
                  <p className="text-xs text-slate-500">
                    None disclosed in parsed filings.
                  </p>
                ) : (
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
                    {upstreamSuppliers.map((sup, idx) => (
                      <div
                        key={`${sup.counterparty_name}-${idx}`}
                        className="rounded bg-[#090d16] border border-slate-800/80 p-2.5 space-y-1"
                      >
                        <div className="flex items-center justify-between gap-1.5 flex-wrap">
                          <span className="text-xs font-semibold text-white">
                            {sup.counterparty_name}
                          </span>
                          <div className="flex items-center gap-1">
                            {sup.is_sole_source && (
                              <span className="px-1.5 py-0.2 rounded bg-rose-500/15 text-rose-300 border border-rose-500/30 text-[10px] font-mono">
                                SOLE SOURCE
                              </span>
                            )}
                            {sup.exposure_pct != null && (
                              <span className="px-1.5 py-0.2 rounded bg-amber-500/15 text-amber-300 text-[10px] font-mono">
                                {sup.exposure_pct}%
                              </span>
                            )}
                          </div>
                        </div>
                        <div className="text-[11px] text-sky-300 font-medium">
                          Input: {sup.material_or_product}
                        </div>
                        {sup.source_quote && (
                          <p className="text-[11px] text-slate-400 italic leading-snug">
                            &ldquo;{sup.source_quote}&rdquo;
                            {sup.source_filing_type && (
                              <span className="not-italic font-mono text-[10px] text-slate-500 ml-1">
                                ({sup.source_filing_type} {sup.source_filing_date})
                              </span>
                            )}
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* 2. Competitors & Sub-Sector Peers */}
              <div className="rounded-lg bg-[#111827] border border-slate-800 p-3.5 space-y-3">
                <div className="text-[11px] font-mono uppercase tracking-wider text-purple-400">
                  2. Competitors &amp; Sub-Sector Peers ({filingCompetitors.length} Filing-Disclosed • {universePeers.length} Universe Peers)
                </div>

                {filingCompetitors.length > 0 && (
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
                    {filingCompetitors.map((comp, idx) => (
                      <div
                        key={`${comp.counterparty_name}-${idx}`}
                        className="rounded bg-[#090d16] border border-slate-800/80 p-2.5 space-y-1"
                      >
                        <div className="flex items-center justify-between gap-1.5 flex-wrap">
                          <span className="text-xs font-semibold text-white">
                            {comp.counterparty_name}
                          </span>
                          <div className="flex items-center gap-1">
                            {comp.is_sole_source && (
                              <span className="px-1.5 py-0.2 rounded bg-purple-500/15 text-purple-300 border border-purple-500/30 text-[10px] font-mono">
                                PRIMARY RIVAL
                              </span>
                            )}
                            {comp.exposure_pct != null && (
                              <span className="px-1.5 py-0.2 rounded bg-sky-500/15 text-sky-300 text-[10px] font-mono">
                                {comp.exposure_pct}% Share
                              </span>
                            )}
                          </div>
                        </div>
                        <div className="text-[11px] text-purple-300 font-medium">
                          Overlap: {comp.material_or_product}
                        </div>
                        {comp.source_quote && (
                          <p className="text-[11px] text-slate-400 italic leading-snug">
                            &ldquo;{comp.source_quote}&rdquo;
                            {comp.source_filing_type && (
                              <span className="not-italic font-mono text-[10px] text-slate-500 ml-1">
                                ({comp.source_filing_type} {comp.source_filing_date})
                              </span>
                            )}
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                )}

                {universePeers.length > 0 ? (
                  <div className={filingCompetitors.length > 0 ? "pt-2 border-t border-slate-800/70" : ""}>
                    <div className="text-[10px] font-mono uppercase text-slate-500 mb-1.5">
                      Tracked Peers in {selectedEquity.sub_sector} (Click to Inspect):
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {universePeers.map((peer) => (
                        <button
                          key={peer.ticker}
                          onClick={() => setSelectedTicker(peer.ticker)}
                          className="px-2.5 py-1 rounded bg-[#090d16] hover:bg-slate-800 border border-slate-700/80 text-xs text-left transition flex items-center gap-1.5"
                        >
                          <span className="font-mono font-semibold text-sky-400">
                            {peer.ticker}
                          </span>
                          <span className="text-slate-300 truncate max-w-[140px]">
                            {peer.company_name}
                          </span>
                          {peer.market_cap_usd != null && (
                            <span className="text-[10px] font-mono text-slate-500">
                              ({formatMarketCap(peer.market_cap_usd)})
                            </span>
                          )}
                        </button>
                      ))}
                    </div>
                  </div>
                ) : (
                  filingCompetitors.length === 0 && (
                    <p className="text-xs text-slate-500">
                      No named competitors extracted from filings yet and no other tracked peers in this sub-sector.
                    </p>
                  )
                )}
              </div>

              {/* 3. Downstream Customers & OEMs */}
              <div className="rounded-lg bg-[#111827] border border-slate-800 p-3.5 space-y-2.5">
                <div className="text-[11px] font-mono uppercase tracking-wider text-emerald-400">
                  3. Downstream Customers &amp; OEMs ({downstreamCustomers.length})
                </div>
                {downstreamCustomers.length === 0 ? (
                  <p className="text-xs text-slate-500">
                    None disclosed in parsed filings.
                  </p>
                ) : (
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
                    {downstreamCustomers.map((cust, idx) => (
                      <div
                        key={`${cust.counterparty_name}-${idx}`}
                        className="rounded bg-[#090d16] border border-slate-800/80 p-2.5 space-y-1"
                      >
                        <div className="flex items-center justify-between gap-1.5 flex-wrap">
                          <span className="text-xs font-semibold text-white">
                            {cust.counterparty_name}
                          </span>
                          <div className="flex items-center gap-1">
                            {cust.is_sole_source && (
                              <span className="px-1.5 py-0.2 rounded bg-amber-500/15 text-amber-300 border border-amber-500/30 text-[10px] font-mono">
                                10%+ CONC.
                              </span>
                            )}
                            {cust.exposure_pct != null && (
                              <span className="px-1.5 py-0.2 rounded bg-emerald-500/15 text-emerald-300 text-[10px] font-mono">
                                {cust.exposure_pct}%
                              </span>
                            )}
                          </div>
                        </div>
                        <div className="text-[11px] text-sky-300 font-medium">
                          Product: {cust.material_or_product}
                        </div>
                        {cust.source_quote && (
                          <p className="text-[11px] text-slate-400 italic leading-snug">
                            &ldquo;{cust.source_quote}&rdquo;
                            {cust.source_filing_type && (
                              <span className="not-italic font-mono text-[10px] text-slate-500 ml-1">
                                ({cust.source_filing_type} {cust.source_filing_date})
                              </span>
                            )}
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* Recent 10-K / 10-Q / Company Profile Intelligence */}
            <div className="space-y-3">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                Recent 10-K, 10-Q &amp; Company Profile Intelligence ({selectedEquityFilings.length})
              </h3>
              {selectedEquityFilings.length === 0 ? (
                <div className="rounded-lg bg-[#111827] border border-slate-800 p-4 text-xs text-slate-400">
                  This ticker is queued for the next SEC EDGAR / AI synthesis batch.
                </div>
              ) : (
                selectedEquityFilings.map((f) => (
                  <div
                    key={f.id}
                    className="rounded-xl bg-[#111827] border border-slate-800 p-4 space-y-3"
                  >
                    <div className="flex items-center justify-between gap-2 flex-wrap">
                      <span className="px-2.5 py-0.5 rounded bg-sky-500/15 text-sky-300 border border-sky-500/30 text-xs font-mono font-semibold">
                        {f.fiscal_period
                          ? `${f.fiscal_period} • ${f.filing_date}`
                          : f.filing_type === "Earnings Brief"
                          ? `Profile & News • As of ${f.filing_date}`
                          : `${f.filing_type} • Filed ${f.filing_date}`}
                      </span>
                      {f.source_url && (
                        <a
                          href={f.source_url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-xs text-sky-400 hover:underline font-mono"
                        >
                          View Filing &nearr;
                        </a>
                      )}
                    </div>

                    <h4 className="text-sm font-semibold text-white">{f.headline}</h4>
                    <p className="text-xs text-slate-300 leading-relaxed">
                      {f.executive_summary}
                    </p>

                    <div className="grid grid-cols-1 gap-2 pt-1 text-xs">
                      {f.pricing_and_margins && (
                        <div className="rounded bg-[#090d16] p-2.5 border border-slate-800/80">
                          <span className="font-mono text-[10px] uppercase text-emerald-400 block mb-0.5">
                            Pricing Power &amp; Margins
                          </span>
                          <span className="text-slate-300">{f.pricing_and_margins}</span>
                        </div>
                      )}
                      {f.capex_and_backlog && (
                        <div className="rounded bg-[#090d16] p-2.5 border border-slate-800/80">
                          <span className="font-mono text-[10px] uppercase text-sky-400 block mb-0.5">
                            Backlog, Qualification &amp; Capex
                          </span>
                          <span className="text-slate-300">{f.capex_and_backlog}</span>
                        </div>
                      )}
                      {f.key_risks && (
                        <div className="rounded bg-[#090d16] p-2.5 border border-slate-800/80">
                          <span className="font-mono text-[10px] uppercase text-rose-400 block mb-0.5">
                            Key Risks
                          </span>
                          <span className="text-slate-300">{f.key_risks}</span>
                        </div>
                      )}
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
