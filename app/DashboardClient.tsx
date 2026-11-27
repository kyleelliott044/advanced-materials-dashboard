"use client";

import { useState, useMemo, useEffect, useRef } from "react";
import StockPriceChart from "./StockPriceChart";
import {
  CompanyProduct,
  ExcludedProduct,
  CompanyProductProfile,
  rollupCompanyProducts,
  buildCommodityReverseIndex,
  buildCategoryPeerIndex,
  RolledUpDrivers,
} from "../lib/productRollup";

export interface FilingMetricsRow {
  ticker: string;
  source_type: string;
  filing_type: string;
  fiscal_period: string;
  filing_date: string;
  period_end_date: string;
  currency: string;
  revenue: string | number | null;
  revenue_yoy_pct: string | number | null;
  gross_profit: string | number | null;
  gross_margin_pct: string | number | null;
  gross_margin_yoy_bps: number | null;
  operating_income: string | number | null;
  operating_margin_pct: string | number | null;
  net_income: string | number | null;
  operating_cash_flow: string | number | null;
  capex: string | number | null;
  capex_pct_rev: string | number | null;
  free_cash_flow: string | number | null;
  rd_expense: string | number | null;
  rd_pct_rev: string | number | null;
  inventory: string | number | null;
  inventory_days: number | null;
  cash_and_equivalents: string | number | null;
  total_debt: string | number | null;
  net_debt: string | number | null;
  source_url: string | null;
}

function formatFilingAmount(
  val: string | number | null | undefined,
  currency: string = "USD"
): string {
  if (val == null || val === "") return "—";
  const num = Number(val);
  if (Number.isNaN(num)) return "—";
  const sign = num < 0 ? "-" : "";
  const abs = Math.abs(num);
  const prefix = currency === "USD" ? "$" : `${currency} `;
  if (abs >= 1e12) return `${sign}${prefix}${(abs / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `${sign}${prefix}${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}${prefix}${(abs / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${sign}${prefix}${(abs / 1e3).toFixed(0)}K`;
  return `${sign}${prefix}${abs.toLocaleString()}`;
}

function formatSignedPct(val: string | number | null | undefined): string {
  if (val == null || val === "") return "—";
  const num = Number(val);
  if (Number.isNaN(num)) return "—";
  return `${num >= 0 ? "+" : ""}${num.toFixed(1)}% YoY`;
}

function formatMarginPct(val: string | number | null | undefined): string {
  if (val == null || val === "") return "—";
  const num = Number(val);
  if (Number.isNaN(num)) return "—";
  return `${num.toFixed(1)}%`;
}

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

type DossierTab = "products" | "value_chain" | "commodities" | "filings" | "metrics";

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

const SHORT_COST_INPUT_NAMES: Record<string, string> = {
  "HG=F": "Copper",
  "NG=F": "Natural Gas",
  "CL=F": "WTI Crude",
  "BZ=F": "Brent Crude",
  "ALI=F": "Aluminum",
  "PL=F": "Platinum",
  "SI=F": "Silver",
  "PA=F": "Palladium",
  "GC=F": "Gold",
  PNICKUSDM: "Nickel",
  PCOBAUSDM: "Cobalt",
  PNGASEUUSDM: "EU Natural Gas",
  WPU101: "Iron & Steel",
  WPU061: "Industrial Chemicals",
  WPU066: "Plastic Resins",
  NDPR_CN: "NdPr Rare Earths",
  LICARB_CN: "Lithium Carbonate",
  GALLIUM_9999: "Gallium",
  GERMANIUM_INGOT: "Germanium",
  POLYSILICON_N: "Polysilicon",
  SPHERICAL_GRAPHITE: "Graphite",
  COBALT_SULFATE: "Cobalt Sulfate",
  CARBON_FIBER_T700: "Carbon Fiber",
};

const SHORT_DEMAND_PROXY_NAMES: Record<string, string> = {
  SMH: "Semiconductors",
  ITA: "Aerospace & Defense",
  LIT: "Lithium & Batteries",
  REMX: "Rare Earths",
  URA: "Uranium & Nuclear",
  SLX: "Specialty Steel",
  IHI: "Medical Devices",
  INDPRO: "Industrial Production",
  PCUOMFGOMFG: "Manufacturing PPI",
  DGS10: "10Y Treasury Yield",
};

function formatShortCostInput(rawLabel: string, code?: string | null): string {
  if (code && SHORT_COST_INPUT_NAMES[code]) {
    return SHORT_COST_INPUT_NAMES[code];
  }
  return rawLabel
    .replace(/\s*\([^)]*\)/g, "")
    .replace(
      /\s+(Futures|Spot|Prices?|Index|Benchmark|Ingot|Oxide|Tow)$/i,
      ""
    )
    .replace(/^(Global|Refined|N-Type)\s+/i, "")
    .trim();
}

function formatShortDemandProxy(
  rawLabel: string,
  code?: string | null
): string {
  if (code && SHORT_DEMAND_PROXY_NAMES[code]) {
    return SHORT_DEMAND_PROXY_NAMES[code];
  }
  return rawLabel
    .replace(/\s*\([^)]*\)/g, "")
    .replace(/\b(ETF|Index|Fund|Trust|Benchmark|Futures|Spot)\b/gi, "")
    .replace(/\s{2,}/g, " ")
    .trim();
}

const MACRO_DRIVER_CODES = new Set([
  "INDPRO",
  "PCUOMFGOMFG",
  "DGS10",
  "SMH",
  "ITA",
  "LIT",
  "REMX",
  "URA",
  "SLX",
  "IHI",
]);

const DEMAND_DRIVER_NAME_RE =
  /\b(demand|build\s*rates?|wafer\s*(starts?|fab)|capex|production|utilization|spending|shipments?|volumes?|end[- ]market|supply\s*chain|yield|interest\s*rate|ppi|index)\b/i;

function isMacroDriverItem(item: RawInputRow): boolean {
  if (MACRO_DRIVER_CODES.has(item.input_code)) return true;
  const cat = (item.category || "").toLowerCase();
  return (
    cat.includes("macro") ||
    cat.includes("demand") ||
    cat.includes("rate") ||
    cat.includes("yield") ||
    cat.includes("index") ||
    cat.includes("sector") ||
    cat.includes("end-market") ||
    cat.includes("etf")
  );
}

const COUNTRY_FLAGS: Record<string, string> = {
  "united states": "🇺🇸",
  usa: "🇺🇸",
  us: "🇺🇸",
  japan: "🇯🇵",
  jp: "🇯🇵",
  germany: "🇩🇪",
  de: "🇩🇪",
  "south korea": "🇰🇷",
  korea: "🇰🇷",
  "republic of korea": "🇰🇷",
  kr: "🇰🇷",
  taiwan: "🇹🇼",
  tw: "🇹🇼",
  china: "🇨🇳",
  cn: "🇨🇳",
  "hong kong": "🇭🇰",
  hk: "🇭🇰",
  "united kingdom": "🇬🇧",
  uk: "🇬🇧",
  "great britain": "🇬🇧",
  gb: "🇬🇧",
  france: "🇫🇷",
  fr: "🇫🇷",
  switzerland: "🇨🇭",
  ch: "🇨🇭",
  sweden: "🇸🇪",
  se: "🇸🇪",
  netherlands: "🇳🇱",
  "the netherlands": "🇳🇱",
  nl: "🇳🇱",
  belgium: "🇧🇪",
  be: "🇧🇪",
  austria: "🇦🇹",
  at: "🇦🇹",
  norway: "🇳🇴",
  no: "🇳🇴",
  finland: "🇫🇮",
  fi: "🇫🇮",
  denmark: "🇩🇰",
  dk: "🇩🇰",
  ireland: "🇮🇪",
  ie: "🇮🇪",
  luxembourg: "🇱🇺",
  lu: "🇱🇺",
  italy: "🇮🇹",
  it: "🇮🇹",
  spain: "🇪🇸",
  es: "🇪🇸",
  canada: "🇨🇦",
  ca: "🇨🇦",
  australia: "🇦🇺",
  au: "🇦🇺",
  india: "🇮🇳",
  in: "🇮🇳",
  brazil: "🇧🇷",
  br: "🇧🇷",
  chile: "🇨🇱",
  cl: "🇨🇱",
  "south africa": "🇿🇦",
  za: "🇿🇦",
  israel: "🇮🇱",
  il: "🇮🇱",
  singapore: "🇸🇬",
  sg: "🇸🇬",
  "saudi arabia": "🇸🇦",
  sa: "🇸🇦",
  mexico: "🇲🇽",
  mx: "🇲🇽",
  poland: "🇵🇱",
  pl: "🇵🇱",
  jersey: "🇯🇪",
  je: "🇯🇪",
  bermuda: "🇧🇲",
  bm: "🇧🇲",
};

function getCountryFlag(country: string | null | undefined): string {
  if (!country) return "";
  return COUNTRY_FLAGS[country.trim().toLowerCase()] || "🌐";
}

function formatMarketCap(usd: number | null): string {
  if (usd == null || usd <= 0) return "—";
  if (usd >= 1e12) return `$${(usd / 1e12).toFixed(2)}T`;
  if (usd >= 1e9) return `$${(usd / 1e9).toFixed(1)}B`;
  if (usd >= 1e6) return `$${(usd / 1e6).toFixed(0)}M`;
  return `$${usd.toLocaleString()}`;
}

interface SleekOption {
  value: string;
  label: string;
  prefix?: string;
}

function SleekSelect({
  value,
  onChange,
  options,
  placeholder = "Select...",
  isFiltered = false,
}: {
  value: string;
  onChange: (val: string) => void;
  options: SleekOption[];
  placeholder?: string;
  isFiltered?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    };
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleKey);
    };
  }, [open]);

  const selected = options.find((o) => o.value === value);

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className={`w-full flex items-center justify-between gap-2 rounded-lg px-3 py-2 text-sm transition-all border ${
          open
            ? "bg-[#0b1120] border-sky-500 ring-2 ring-sky-500/20 text-white"
            : isFiltered
            ? "bg-sky-950/30 border-sky-500/50 text-sky-200 hover:border-sky-400"
            : "bg-[#090d16] border-slate-700/80 text-slate-200 hover:border-slate-600 hover:bg-[#0c1220]"
        }`}
      >
        <span className="flex items-center gap-2 truncate">
          {selected?.prefix && (
            <span className="text-sm leading-none shrink-0">
              {selected.prefix}
            </span>
          )}
          <span className="truncate">{selected?.label || placeholder}</span>
        </span>
        <svg
          className={`w-3.5 h-3.5 text-slate-400 shrink-0 transition-transform duration-150 ${
            open ? "rotate-180 text-sky-400" : ""
          }`}
          viewBox="0 0 20 20"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <path
            d="M6 8l4 4 4-4"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>

      {open && (
        <div className="absolute left-0 right-0 mt-1.5 z-40 rounded-xl bg-[#0d1424]/95 backdrop-blur-xl border border-slate-700/90 shadow-2xl py-1 max-h-64 overflow-y-auto">
          {options.map((opt) => {
            const active = opt.value === value;
            return (
              <button
                key={opt.value}
                type="button"
                onClick={() => {
                  onChange(opt.value);
                  setOpen(false);
                }}
                className={`w-full flex items-center justify-between gap-2 px-3 py-2 text-left text-xs transition-colors ${
                  active
                    ? "bg-sky-500/15 text-sky-300 font-medium"
                    : "text-slate-300 hover:bg-slate-800/80 hover:text-white"
                }`}
              >
                <span className="flex items-center gap-2 truncate">
                  {opt.prefix && (
                    <span className="text-sm leading-none shrink-0">
                      {opt.prefix}
                    </span>
                  )}
                  <span className="truncate">{opt.label}</span>
                </span>
                {active && (
                  <span className="text-sky-400 font-mono text-[11px] shrink-0">
                    ✓
                  </span>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default function DashboardClient({
  equities: rawEquities,
  inputs,
  sensitivities,
  filings: rawFilings,
  narrative,
  supplyChain = [],
  commodityDrivers = [],
  products = [],
  excludedProducts = [],
  productProfiles = [],
  filingMetrics = [],
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
  filingMetrics?: FilingMetricsRow[];
}) {
  // Deduplicate repeated company rows (e.g., 120290 vs 120290.KS)
  const equities = useMemo(() => {
    const normalizeName = (name: string) =>
      name
        .toLowerCase()
        .replace(
          /\b(inc|corp|corporation|co|ltd|limited|plc|ag|se|sa|holdings?|group)\b/g,
          ""
        )
        .replace(/[^a-z0-9]/g, "");

    const byKey = new Map<string, EquityRow>();
    for (const eq of rawEquities) {
      const normName = normalizeName(eq.company_name);
      const key = normName || eq.ticker.split(".")[0].toUpperCase();
      const existing = byKey.get(key);
      if (!existing) {
        byKey.set(key, eq);
      } else {
        const existingHasSuffix = existing.ticker.includes(".");
        const currentHasSuffix = eq.ticker.includes(".");
        if (
          (!existingHasSuffix && currentHasSuffix) ||
          ((existing.market_cap_usd ?? 0) === 0 &&
            (eq.market_cap_usd ?? 0) > 0)
        ) {
          byKey.set(key, eq);
        }
      }
    }
    return Array.from(byKey.values());
  }, [rawEquities]);

  // Exclude FY2024 or older filings defensively
  const filings = useMemo(() => {
    return rawFilings.filter((f) => {
      if (f.fiscal_period && /\b(?:fy\s*)?202[0-4]\b/i.test(f.fiscal_period))
        return false;
      if (f.filing_date && f.filing_date < "2025-01-01") return false;
      if (
        ["10-K", "20-F", "40-F", "10-K/A", "20-F/A"].includes(f.filing_type) &&
        f.filing_date &&
        f.filing_date < "2025-06-01"
      ) {
        return false;
      }
      return true;
    });
  }, [rawFilings]);

  const [activeTab, setActiveTab] = useState<"screener" | "feed">("screener");
  const [narrativeTab, setNarrativeTab] = useState<
    "executive" | "ai" | "aero" | "commodities" | "ev"
  >("executive");
  const [driverFilterTab, setDriverFilterTab] = useState<
    "commodities" | "macro"
  >("commodities");
  const [dossierTab, setDossierTab] = useState<DossierTab>("products");
  const [showExcluded, setShowExcluded] = useState(false);

  const [search, setSearch] = useState("");
  const [sectorFilter, setSectorFilter] = useState("ALL");
  const [countryFilter, setCountryFilter] = useState("ALL");
  const [contractFilter, setContractFilter] = useState("ALL");
  const [sortBy, setSortBy] = useState<SortOption>("mcap_desc");
  const [selectedCommodity, setSelectedCommodity] = useState<string | null>(
    null
  );
  const [selectedProductCategory, setSelectedProductCategory] = useState<
    string | null
  >(null);
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

  // Open company dossier when a Key Ticker chip is clicked in ScopedInsightsPanel
  useEffect(() => {
    const handleSelectTicker = (e: Event) => {
      const customEvent = e as CustomEvent<string>;
      if (customEvent.detail) {
        setSelectedTicker(customEvent.detail);
      }
    };
    window.addEventListener("terminal:select-ticker", handleSelectTicker);
    return () =>
      window.removeEventListener("terminal:select-ticker", handleSelectTicker);
  }, []);

  // Close company  on Escape key
  useEffect(() => {
    if (!selectedTicker) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSelectedTicker(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selectedTicker]);

  // Reset dossier sub-tab when switching companies
  useEffect(() => {
    if (selectedTicker) {
      setDossierTab("products");
      setShowExcluded(false);
    }
  }, [selectedTicker]);

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

  // Separate filter cards into Commodities vs. Macro / Demand Drivers
  const { commodityInputs, macroDriverInputs, macroCodeSet } = useMemo(() => {
    const commodities: RawInputRow[] = [];
    const macro: RawInputRow[] = [];
    const codes = new Set<string>(MACRO_DRIVER_CODES);
    for (const item of inputs) {
      if (isMacroDriverItem(item)) {
        macro.push(item);
        codes.add(item.input_code);
      } else {
        commodities.push(item);
      }
    }
    return {
      commodityInputs: commodities,
      macroDriverInputs: macro,
      macroCodeSet: codes,
    };
  }, [inputs]);

  const displayedFilterInputs =
    driverFilterTab === "commodities" ? commodityInputs : macroDriverInputs;

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

  // Product rollup per company (strictly status === 'active')
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

  const sectorOptions = useMemo<SleekOption[]>(() => {
    const unique = new Set(
      equities.map((e) => e.sub_sector).filter((s): s is string => Boolean(s))
    );
    return [
      { value: "ALL", label: "All Sub-Sectors" },
      ...Array.from(unique)
        .sort()
        .map((sec) => ({ value: sec, label: sec })),
    ];
  }, [equities]);

  const countryOptions = useMemo<SleekOption[]>(() => {
    const unique = new Set(
      equities.map((e) => e.country).filter((c): c is string => Boolean(c))
    );
    return [
      { value: "ALL", label: "All Countries", prefix: "🌍" },
      ...Array.from(unique)
        .sort()
        .map((c) => ({
          value: c,
          label: c,
          prefix: getCountryFlag(c),
        })),
    ];
  }, [equities]);

  const contractOptions: SleekOption[] = [
    { value: "ALL", label: "All Contracts" },
    { value: "take_or_pay", label: "Take-or-Pay" },
    { value: "pass_through", label: "Pass-Through" },
    { value: "fixed_spread", label: "Fixed Spread" },
  ];

  const sortOptions: SleekOption[] = [
    { value: "mcap_desc", label: "Market Cap USD (High → Low)" },
    { value: "mcap_asc", label: "Market Cap USD (Low → High)" },
    { value: "products_desc", label: "Active Products (High → Low)" },
    { value: "rev_desc", label: "Adv. Mat. Rev % (High → Low)" },
    { value: "rev_asc", label: "Adv. Mat. Rev % (Low → High)" },
    { value: "country_asc", label: "Country (A → Z)" },
    { value: "moat_desc", label: "Est. Moat (High → Low)" },
    { value: "ticker_asc", label: "Ticker (A → Z)" },
  ];

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

  const tickerMetricsMap = useMemo(() => {
    const map: Record<string, FilingMetricsRow> = {};
    for (const m of filingMetrics) {
      map[m.ticker] = m;
    }
    return map;
  }, [filingMetrics]);

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

  // Unified company-level cost_inputs & demand_proxies (clean short labels without "ETF"/"Index")
  const companyDriversByRole = useMemo(() => {
    const map: Record<
      string,
      {
        costInputs: { code: string | null; label: string; detail?: string }[];
        demandProxies: { code: string | null; label: string; detail?: string }[];
        fromProductRollup: boolean;
      }
    > = {};

    const dedupeByLabel = (
      items: { code: string | null; label: string; detail?: string }[]
    ) => {
      const seen = new Set<string>();
      return items.filter((it) => {
        const key = it.label.toLowerCase();
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
    };

    for (const eq of equities) {
      const rollup = tickerRollupMap[eq.ticker];
      if (rollup && rollup.isProductDerived) {
        map[eq.ticker] = {
          costInputs: dedupeByLabel(
            rollup.costInputCodes.map((code) => ({
              code,
              label: formatShortCostInput(getCommodityDisplayName(code), code),
            }))
          ),
          demandProxies: dedupeByLabel(
            rollup.demandProxyCodes.map((code) => ({
              code,
              label: formatShortDemandProxy(
                getCommodityDisplayName(code),
                code
              ),
            }))
          ),
          fromProductRollup: true,
        };
        continue;
      }

      const aiDrivers = tickerDriversMap[eq.ticker] || [];
      if (aiDrivers.length > 0) {
        const costInputs = dedupeByLabel(
          aiDrivers
            .filter((d) => d.exposure_role === "Cost Input")
            .map((d) => ({
              code: d.matched_input_code,
              label: formatShortCostInput(
                d.commodity_name,
                d.matched_input_code
              ),
              detail: d.mechanism_detail,
            }))
        );
        const demandProxies = dedupeByLabel(
          aiDrivers
            .filter((d) => d.exposure_role !== "Cost Input")
            .map((d) => ({
              code: d.matched_input_code,
              label: formatShortDemandProxy(
                d.commodity_name,
                d.matched_input_code
              ),
              detail: d.mechanism_detail,
            }))
        );
        map[eq.ticker] = { costInputs, demandProxies, fromProductRollup: false };
      } else {
        const subRules = sectorSensitivitiesMap[eq.sub_sector || ""] || [];
        const costInputs = dedupeByLabel(
          subRules
            .filter((r) => r.relationship_type === "Cost Input")
            .map((r) => ({
              code: r.input_code,
              label: formatShortCostInput(
                getCommodityDisplayName(r.input_code),
                r.input_code
              ),
              detail: r.mechanism_note,
            }))
        );
        const demandProxies = dedupeByLabel(
          subRules
            .filter((r) => r.relationship_type !== "Cost Input")
            .map((r) => ({
              code: r.input_code,
              label: formatShortDemandProxy(
                getCommodityDisplayName(r.input_code),
                r.input_code
              ),
              detail: r.mechanism_note,
            }))
        );
        map[eq.ticker] = { costInputs, demandProxies, fromProductRollup: false };
      }
    }

    return map;
  }, [
    equities,
    tickerRollupMap,
    tickerDriversMap,
    sectorSensitivitiesMap,
    inputNameMap,
  ]);

  const hasActiveFilters = Boolean(
    countryFilter !== "ALL" ||
      sectorFilter !== "ALL" ||
      contractFilter !== "ALL" ||
      selectedCommodity ||
      selectedProductCategory ||
      search.trim()
  );

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
          matchesCommodity =
            Boolean(revHit) || matchesSubRule || matchesAiDriver;
        }
      }

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
        matchesCategory
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

  const selectedEquityMetrics = selectedTicker
    ? tickerMetricsMap[selectedTicker] || null
    : null;

  const selectedEquityDrivers = selectedTicker
    ? tickerDriversMap[selectedTicker] || []
    : [];

  const selectedEquitySensitivities = selectedEquity?.sub_sector
    ? sectorSensitivitiesMap[selectedEquity.sub_sector] || []
    : [];

  // Separate actual commodities from end-market / macro demand drivers in the dossier
  const {
    dossierAiCommodities,
    dossierAiDemandDrivers,
    dossierSectorCommodities,
    dossierSectorDemandDrivers,
  } = useMemo(() => {
    const isAiDemandDriver = (d: CommodityDriverRow) => {
      if (d.matched_input_code && macroCodeSet.has(d.matched_input_code)) {
        return true;
      }
      if (
        !d.matched_input_code &&
        d.exposure_role === "Revenue Driver" &&
        d.impact_direction === "Direct (Demand)" &&
        DEMAND_DRIVER_NAME_RE.test(d.commodity_name)
      ) {
        return true;
      }
      return false;
    };

    const aiCommodities: CommodityDriverRow[] = [];
    const aiDemandDrivers: CommodityDriverRow[] = [];
    for (const d of selectedEquityDrivers) {
      if (isAiDemandDriver(d)) {
        aiDemandDrivers.push(d);
      } else {
        aiCommodities.push(d);
      }
    }

    const sectorCommodities: SensitivityRow[] = [];
    const sectorDemandDrivers: SensitivityRow[] = [];
    for (const s of selectedEquitySensitivities) {
      if (macroCodeSet.has(s.input_code)) {
        sectorDemandDrivers.push(s);
      } else {
        sectorCommodities.push(s);
      }
    }

    return {
      dossierAiCommodities: aiCommodities,
      dossierAiDemandDrivers: aiDemandDrivers,
      dossierSectorCommodities: sectorCommodities,
      dossierSectorDemandDrivers: sectorDemandDrivers,
    };
  }, [selectedEquityDrivers, selectedEquitySensitivities, macroCodeSet]);

  const selectedEquitySupplyChain = selectedTicker
    ? tickerSupplyChainMap[selectedTicker] || []
    : [];

  // Deduplicate repeated counterparties (e.g., Evonik or downstream OEMs) per direction
  const { upstreamSuppliers, filingCompetitors, downstreamCustomers } =
    useMemo(() => {
      const groupByCounterparty = (rows: SupplyChainRow[]) => {
        const map = new Map<string, SupplyChainRow>();
        for (const r of rows) {
          const key = r.counterparty_name
            .toLowerCase()
            .replace(
              /\b(ag|se|sa|nv|inc|corp|corporation|co|ltd|limited|plc|gmbh|kgaa|holdings?|group|industries)\b/g,
              ""
            )
            .replace(/[^a-z0-9]/g, "");
          const bucket = key || r.counterparty_name.toLowerCase();
          const existing = map.get(bucket);
          if (!existing) {
            map.set(bucket, { ...r });
          } else {
            if (
              r.material_or_product &&
              !existing.material_or_product
                .toLowerCase()
                .includes(r.material_or_product.toLowerCase())
            ) {
              existing.material_or_product = `${existing.material_or_product} • ${r.material_or_product}`;
            }
            existing.is_sole_source =
              existing.is_sole_source || r.is_sole_source;
            if (
              r.exposure_pct != null &&
              (existing.exposure_pct == null ||
                r.exposure_pct > existing.exposure_pct)
            ) {
              existing.exposure_pct = r.exposure_pct;
            }
          }
        }
        return Array.from(map.values());
      };

      return {
        upstreamSuppliers: groupByCounterparty(
          selectedEquitySupplyChain.filter(
            (s) => s.direction === "upstream_supplier"
          )
        ),
        filingCompetitors: groupByCounterparty(
          selectedEquitySupplyChain.filter((s) => s.direction === "competitor")
        ),
        downstreamCustomers: groupByCounterparty(
          selectedEquitySupplyChain.filter(
            (s) => s.direction === "downstream_customer"
          )
        ),
      };
    }, [selectedEquitySupplyChain]);

  const universePeers = useMemo(() => {
    if (!selectedEquity?.sub_sector) return [];
    return equities.filter(
      (e) =>
        e.sub_sector === selectedEquity.sub_sector &&
        e.ticker !== selectedEquity.ticker
    );
  }, [equities, selectedEquity]);

  const handleSelectCommodityFromDossier = (code: string) => {
    setDriverFilterTab(macroCodeSet.has(code) ? "macro" : "commodities");
    setSelectedCommodity(code);
    setSelectedProductCategory(null);
    setActiveTab("screener");
    setSelectedTicker(null);
  };

  const handleSelectCategoryFromDossier = (category: string) => {
    setSelectedProductCategory(category);
    setSelectedCommodity(null);
    setActiveTab("screener");
    setSelectedTicker(null);
  };

  const activeProductsCount = selectedEquityProducts.filter(
    (p) => p.status === "active"
  ).length;
  const totalValueChainCount =
    upstreamSuppliers.length +
    filingCompetitors.length +
    downstreamCustomers.length +
    universePeers.length;
  const totalCommodityCount =
    selectedEquityDrivers.length > 0
      ? selectedEquityDrivers.length
      : selectedEquitySensitivities.length;

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

        {/* Interactive Commodities & Macro Drivers Filter Tabs */}
        <section>
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-3">
            <div className="flex items-center gap-3 flex-wrap">
              <div className="inline-flex rounded-xl bg-[#111827] border border-slate-800 p-1">
                <button
                  type="button"
                  onClick={() => setDriverFilterTab("commodities")}
                  className={`px-3 py-1.5 rounded-lg text-xs font-medium transition flex items-center gap-1.5 ${
                    driverFilterTab === "commodities"
                      ? "bg-sky-500 text-white shadow-sm"
                      : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <span>Commodities</span>
                  <span
                    className={`px-1.5 py-0.2 rounded text-[10px] font-mono ${
                      driverFilterTab === "commodities"
                        ? "bg-sky-950/50 text-white"
                        : "bg-slate-800 text-slate-400"
                    }`}
                  >
                    {commodityInputs.length}
                  </span>
                </button>
                <button
                  type="button"
                  onClick={() => setDriverFilterTab("macro")}
                  className={`px-3 py-1.5 rounded-lg text-xs font-medium transition flex items-center gap-1.5 ${
                    driverFilterTab === "macro"
                      ? "bg-sky-500 text-white shadow-sm"
                      : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <span>Macro Drivers</span>
                  <span
                    className={`px-1.5 py-0.2 rounded text-[10px] font-mono ${
                      driverFilterTab === "macro"
                        ? "bg-sky-950/50 text-white"
                        : "bg-slate-800 text-slate-400"
                    }`}
                  >
                    {macroDriverInputs.length}
                  </span>
                </button>
              </div>

              <span className="text-xs text-slate-400">
                {driverFilterTab === "commodities"
                  ? "Click a raw material or feedstock to filter exposed products & companies"
                  : "Click a macro indicator or end-market proxy to filter exposed companies"}
              </span>

              {selectedCommodity && (
                <button
                  onClick={() => setSelectedCommodity(null)}
                  className="text-xs font-mono text-sky-400 hover:text-sky-300 underline"
                >
                  Clear Filter ({getCommodityDisplayName(selectedCommodity)})
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
            {displayedFilterInputs.map((item) => {
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
                      {item.category ||
                        (driverFilterTab === "macro"
                          ? "Macro Driver"
                          : "Commodity")}
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
                  className="w-full rounded-lg bg-[#090d16] border border-slate-700/80 px-3.5 py-2 text-sm text-white placeholder-slate-500 hover:border-slate-600 focus:outline-none focus:border-sky-500 focus:ring-2 focus:ring-sky-500/20 transition"
                />
              </div>

              <div>
                <label className="block text-[11px] font-mono uppercase text-slate-400 mb-1.5">
                  Country
                </label>
                <SleekSelect
                  value={countryFilter}
                  onChange={setCountryFilter}
                  options={countryOptions}
                  isFiltered={countryFilter !== "ALL"}
                />
              </div>

              <div>
                <label className="block text-[11px] font-mono uppercase text-slate-400 mb-1.5">
                  Sub-Sector
                </label>
                <SleekSelect
                  value={sectorFilter}
                  onChange={setSectorFilter}
                  options={sectorOptions}
                  isFiltered={sectorFilter !== "ALL"}
                />
              </div>

              <div>
                <label className="block text-[11px] font-mono uppercase text-slate-400 mb-1.5">
                  Est. Pricing Model
                </label>
                <SleekSelect
                  value={contractFilter}
                  onChange={setContractFilter}
                  options={contractOptions}
                  isFiltered={contractFilter !== "ALL"}
                />
              </div>

              <div>
                <label className="block text-[11px] font-mono uppercase text-slate-400 mb-1.5">
                  Sort By
                </label>
                <SleekSelect
                  value={sortBy}
                  onChange={(val) => setSortBy(val as SortOption)}
                  options={sortOptions}
                />
              </div>
            </div>

            {/* Active Reverse-Index Banner */}
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
                              Object.values(
                                commodityReverseIndex[selectedCommodity] || {}
                              ).reduce((acc, h) => acc + h.products.length, 0)
                            } active product(s) in ${
                              Object.keys(
                                commodityReverseIndex[selectedCommodity] || {}
                              ).length
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
                        <span className="text-white font-medium">
                          {selectedProductCategory}
                        </span>{" "}
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
              <div className="px-5 py-3 border-b border-slate-800 flex items-center justify-between gap-3 flex-wrap text-xs text-slate-400">
                <div className="flex items-center gap-3">
                  <span>
                    Showing <strong className="text-white">{filteredEquities.length}</strong> of{" "}
                    {equities.length} companies
                  </span>
                  {hasActiveFilters && (
                    <button
                      onClick={() => {
                        setSearch("");
                        setCountryFilter("ALL");
                        setSectorFilter("ALL");
                        setContractFilter("ALL");
                        setSelectedCommodity(null);
                        setSelectedProductCategory(null);
                      }}
                      className="text-xs font-mono text-sky-400 hover:text-sky-300 underline"
                    >
                      Reset Filters
                    </button>
                  )}
                </div>
                <span className="text-sky-400 font-medium">
                  Click any company row for Chart, Products &amp; Filing Dossier &rarr;
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
                        ? commodityReverseIndex[selectedCommodity]?.[eq.ticker]
                            ?.products || []
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
                            <div className="font-medium text-white">
                              {eq.company_name}
                            </div>
                            <div className="text-xs text-slate-400">
                              {eq.sub_sector}
                            </div>
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
                            {eq.country ? (
                              <span className="inline-flex items-center gap-1.5">
                                <span className="text-sm leading-none">
                                  {getCountryFlag(eq.country)}
                                </span>
                                <span>{eq.country}</span>
                              </span>
                            ) : (
                              "—"
                            )}
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

                          {/* Cost Inputs (Short Names, Raw Materials -> Margin) */}
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
                                  {roleDrivers.costInputs
                                    .slice(0, 3)
                                    .map((ci, idx) => (
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
                                  {rollup &&
                                    rollup.unloggedMaterials.length > 0 && (
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

                          {/* Demand Proxies (Clean Labels without "ETF" / "Index") */}
                          <td className="py-3.5 px-4">
                            <div className="flex flex-wrap gap-1.5 max-w-[210px]">
                              {roleDrivers.demandProxies.length === 0 ? (
                                <span className="text-xs text-slate-500">—</span>
                              ) : (
                                <>
                                  {roleDrivers.demandProxies
                                    .slice(0, 3)
                                    .map((dp, idx) => (
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
                                {eqFilings.length} Brief
                                {eqFilings.length > 1 ? "s" : ""}
                              </span>
                            ) : (
                              <span className="text-xs text-slate-600 font-mono">
                                Queued
                              </span>
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
                Showing {filteredFilings.length} of {filings.length} briefs across{" "}
                {coveredCompanyCount} companies • Click any card for full dossier
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

      {/* Clean Tabbed Slide-Over Company Dossier Drawer */}
      {selectedEquity && (
        <div
          onClick={() => setSelectedTicker(null)}
          className="fixed inset-0 z-50 flex justify-end bg-black/75 backdrop-blur-sm"
        >
          <div
            onClick={(e) => e.stopPropagation()}
            className="w-full max-w-3xl bg-[#0b111e] border-l border-slate-800 h-full flex flex-col shadow-2xl"
          >
            {/* Sticky Clean Dossier Header */}
            <div className="px-6 pt-5 pb-4 bg-[#0d1424] border-b border-slate-800/90 space-y-3.5 shrink-0">
              <div className="flex items-start justify-between gap-4">
                <div className="space-y-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-xl font-mono font-bold text-sky-400">
                      {selectedEquity.ticker}
                    </span>
                    {selectedEquity.sub_sector && (
                      <span className="px-2.5 py-0.5 rounded-full text-xs bg-slate-800/90 text-slate-300 border border-slate-700/70">
                        {selectedEquity.sub_sector}
                      </span>
                    )}
                    {selectedEquity.country && (
                      <span className="px-2.5 py-0.5 rounded-full text-xs bg-slate-800/90 text-slate-300 border border-slate-700/70">
                        {selectedEquity.country}
                      </span>
                    )}
                  </div>
                  <h2 className="text-lg font-semibold text-white">
                    {selectedEquity.company_name}
                  </h2>
                </div>

                <button
                  onClick={() => setSelectedTicker(null)}
                  className="px-3 py-1.5 rounded-lg bg-slate-800/90 hover:bg-slate-700 text-xs font-medium text-slate-200 border border-slate-700/80 transition shrink-0"
                >
                  Close ✕
                </button>
              </div>

              {/* Interactive 1M / 6M / 1Y / 5Y Price Chart */}
              <StockPriceChart ticker={selectedEquity.ticker} />

              {/* Compact 4-Metric KPI Strip */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
                <div className="rounded-lg bg-[#090d16] border border-slate-800/80 px-3 py-2">
                  <div className="text-[10px] font-mono uppercase text-slate-500">
                    Market Cap
                  </div>
                  <div className="text-xs font-mono font-semibold text-white mt-0.5">
                    {formatMarketCap(selectedEquity.market_cap_usd)}
                  </div>
                </div>
                <div className="rounded-lg bg-[#090d16] border border-slate-800/80 px-3 py-2">
                  <div className="text-[10px] font-mono uppercase text-slate-500">
                    Adv. Mat. Rev
                  </div>
                  <div className="text-xs font-mono font-semibold text-emerald-400 mt-0.5">
                    {selectedEquity.adv_mat_rev_pct != null
                      ? `${selectedEquity.adv_mat_rev_pct}%`
                      : "—"}
                  </div>
                </div>
                <div className="rounded-lg bg-[#090d16] border border-slate-800/80 px-3 py-2">
                  <div className="text-[10px] font-mono uppercase text-slate-500">
                    Est. Pricing
                  </div>
                  <div className="text-xs font-medium text-slate-200 mt-0.5 truncate">
                    {CONTRACT_BADGES[selectedEquity.contract_type || ""]
                      ?.label ||
                      selectedEquity.contract_type ||
                      "Standard"}
                  </div>
                </div>
                <div className="rounded-lg bg-[#090d16] border border-slate-800/80 px-3 py-2">
                  <div className="text-[10px] font-mono uppercase text-slate-500">
                    Est. Moat
                  </div>
                  <div className="text-xs font-mono font-semibold text-sky-300 mt-0.5">
                    {selectedEquity.qualification_years != null
                      ? `${Number(selectedEquity.qualification_years).toFixed(1)} yrs`
                      : "—"}
                  </div>
                </div>
              </div>

             {/* Clean Segmented Navigation Tabs */}
              <div className="flex items-center gap-1.5 overflow-x-auto pt-0.5">
                {[
                  {
                    id: "products" as DossierTab,
                    label: "Products",
                    count: activeProductsCount,
                  },
                  {
                    id: "value_chain" as DossierTab,
                    label: "Value Chain & Peers",
                    count: totalValueChainCount,
                  },
                  {
                    id: "commodities" as DossierTab,
                    label: "Commodity Exposure",
                    count: totalCommodityCount,
                  },
                  {
                    id: "filings" as DossierTab,
                    label: "Filings & Briefs",
                    count: selectedEquityFilings.length,
                  },
                  {
                    id: "metrics" as DossierTab,
                    label: "Key Metrics",
                    count: selectedEquityMetrics ? 1 : 0,
                  },
                ].map((tab) => {
                  const active = dossierTab === tab.id;
                  return (
                    <button
                      key={tab.id}
                      type="button"
                      onClick={() => setDossierTab(tab.id)}
                      className={`px-3 py-1.5 rounded-lg text-xs font-medium transition flex items-center gap-1.5 whitespace-nowrap ${
                        active
                          ? "bg-sky-500 text-white shadow-sm"
                          : "bg-[#090d16] text-slate-400 border border-slate-800 hover:text-slate-200 hover:border-slate-700"
                      }`}
                    >
                      <span>{tab.label}</span>
                      <span
                        className={`px-1.5 py-0.2 rounded text-[10px] font-mono ${
                          active
                            ? "bg-sky-950/50 text-white"
                            : "bg-slate-800 text-slate-400"
                        }`}
                      >
                        {tab.count}
                      </span>
                    </button>
                  );
                })}
              </div>

            {/* Scrollable Dossier Tab Body */}
            <div className="flex-1 overflow-y-auto p-6 space-y-5">
              {/* TAB 1: PRODUCTS */}
              {dossierTab === "products" && (
                <div className="space-y-4">
                  {selectedEquityNote && (
                    <div className="rounded-xl bg-slate-900/80 border border-slate-800 px-4 py-3 text-xs text-slate-300 leading-relaxed">
                      {selectedEquityNote}
                    </div>
                  )}

                  {selectedEquityProducts.length === 0 ? (
                    <div className="rounded-xl bg-[#111827] border border-slate-800 p-6 text-center space-y-2">
                      <p className="text-xs text-slate-400">
                        Product-level decomposition has not been generated for{" "}
                        <span className="text-white font-mono">
                          {selectedEquity.ticker}
                        </span>{" "}
                        yet.
                      </p>
                      <button
                        type="button"
                        onClick={() => setDossierTab("filings")}
                        className="text-xs font-mono text-sky-400 hover:underline"
                      >
                        View parsed filings &amp; briefs &rarr;
                      </button>
                    </div>
                  ) : (
                    <>
                      <div className="flex items-center justify-between text-[11px] text-slate-400">
                        <span>
                          Chain: <strong className="text-amber-300">Raw Material</strong> &rarr;{" "}
                          <strong className="text-white">Product</strong> &rarr;{" "}
                          <strong className="text-sky-400">Downstream End-Demand</strong>
                        </span>
                        <span className="font-mono text-[10px]">
                          Click solid chips to filter
                        </span>
                      </div>

                      <div className="space-y-3">
                        {selectedEquityProducts.map((prod) => {
                          const proxyCode = prod.downstream?.demand_proxy_ref;
                          const isProxyValid = Boolean(
                            proxyCode && validRegistryCodes.has(proxyCode)
                          );

                          return (
                            <div
                              key={prod.id}
                              className={`rounded-xl border p-4 space-y-3 transition ${
                                prod.status === "divested"
                                  ? "bg-slate-950/60 border-slate-800/60 opacity-60"
                                  : "bg-[#111827]/90 border-slate-800 hover:border-slate-700"
                              }`}
                            >
                              {/* Card Header: Product Title, Category & Status */}
                              <div className="flex items-start justify-between gap-3 flex-wrap">
                                <div className="space-y-1">
                                  <div className="flex items-center gap-2 flex-wrap">
                                    <span className="text-sm font-semibold text-white">
                                      {prod.name}
                                    </span>
                                    <button
                                      type="button"
                                      onClick={() =>
                                        handleSelectCategoryFromDossier(
                                          prod.category
                                        )
                                      }
                                      title="Click to view all peer companies in this product category"
                                      className="px-2 py-0.5 rounded bg-purple-500/15 hover:bg-purple-500/25 text-purple-300 border border-purple-500/35 text-[11px] font-medium transition cursor-pointer"
                                    >
                                      {prod.category}
                                    </button>
                                  </div>
                                  <div className="text-[11px] font-mono text-slate-400">
                                    BU: {prod.business_unit}
                                  </div>
                                </div>

                                <div className="flex items-center gap-2 font-mono text-[10px]">
                                  <span className="text-slate-400">
                                    {prod.as_of}
                                  </span>
                                  <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-300">
                                    {prod.conf} ({prod.src.type})
                                  </span>
                                  <span
                                    className={`px-1.5 py-0.5 rounded uppercase ${
                                      prod.status === "active"
                                        ? "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30"
                                        : "bg-rose-500/15 text-rose-300 border border-rose-500/30"
                                    }`}
                                  >
                                    {prod.status}
                                  </span>
                                </div>
                              </div>

                              {/* Visual Flow Pipeline: [Raw Material] -> [Product] -> [Downstream] */}
                              <div className="rounded-lg bg-[#090d16] border border-slate-800/80 p-3 flex flex-wrap items-center gap-2 text-xs">
                                <div className="flex flex-wrap items-center gap-1.5">
                                  {(prod.raw_materials || []).map(
                                    (rm, rIdx) => {
                                      const isLogged = Boolean(
                                        rm.commodity_ref &&
                                          validRegistryCodes.has(
                                            rm.commodity_ref
                                          )
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
                                            )} (${rm.role} • ${rm.basis}). Click to filter companies.`}
                                            className="px-2.5 py-1 rounded-md bg-amber-500/20 hover:bg-amber-500/30 text-amber-200 border border-solid border-amber-400/60 font-medium transition cursor-pointer"
                                          >
                                            {formatShortCostInput(
                                              rm.name,
                                              rm.commodity_ref
                                            )}
                                          </button>
                                        );
                                      }
                                      return (
                                        <span
                                          key={`${rm.name}-${rIdx}`}
                                          title={`Unlogged raw material (${rm.role} • ${rm.basis})`}
                                          className="px-2.5 py-1 rounded-md bg-slate-900/70 text-slate-400 border border-dashed border-slate-600 inline-flex items-center gap-1.5"
                                        >
                                          <span>{rm.name}</span>
                                          <span className="text-[9px] font-mono uppercase text-slate-500">
                                            unlogged
                                          </span>
                                        </span>
                                      );
                                    }
                                  )}
                                </div>

                                <span className="text-slate-500 font-mono">
                                  &rarr;
                                </span>

                                <span className="px-2.5 py-1 rounded-md bg-slate-800 text-white border border-slate-600 font-semibold">
                                  {prod.name}
                                </span>

                                <span className="text-slate-500 font-mono">
                                  &rarr;
                                </span>

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
                                    }). Click to filter companies.`}
                                    className="px-3 py-1 rounded-md bg-sky-500 text-slate-950 font-semibold shadow-sm shadow-sky-500/25 hover:bg-sky-400 transition cursor-pointer"
                                  >
                                    {prod.downstream.end_demand}
                                  </button>
                                ) : (
                                  <span
                                    title={`${prod.downstream.process_step} -> ${prod.downstream.end_product}`}
                                    className="px-3 py-1 rounded-md bg-sky-500 text-slate-950 font-semibold shadow-sm shadow-sky-500/25"
                                  >
                                    {prod.downstream.end_demand}
                                  </span>
                                )}
                              </div>

                              {/* Clean 2-Column Technical Specs & Peers */}
                              <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1.5 text-[11px] text-slate-400">
                                <div>
                                  <span className="text-slate-500">
                                    Process &amp; Output:
                                  </span>{" "}
                                  <span className="text-slate-300">
                                    {prod.downstream.process_step} &rarr;{" "}
                                    {prod.downstream.end_product}
                                  </span>
                                </div>
                                <div>
                                  <span className="text-slate-500">
                                    Switching Barrier:
                                  </span>{" "}
                                  <span className="text-slate-300">
                                    {prod.advanced.switching_barrier}
                                  </span>
                                </div>
                                <div className="sm:col-span-2">
                                  <span className="text-slate-500">
                                    Performance Spec:
                                  </span>{" "}
                                  <span className="text-slate-300">
                                    {prod.advanced.performance_spec}
                                  </span>
                                </div>
                              </div>

                              {prod.peers && prod.peers.length > 0 && (
                                <div className="flex flex-wrap items-center gap-1.5 pt-1 border-t border-slate-800/70">
                                  <span className="text-[10px] font-mono uppercase text-slate-500">
                                    Product Peers:
                                  </span>
                                  {prod.peers.map((peerTk) => (
                                    <button
                                      key={peerTk}
                                      type="button"
                                      onClick={() => setSelectedTicker(peerTk)}
                                      className="px-2 py-0.5 rounded bg-[#090d16] hover:bg-slate-800 text-sky-400 border border-slate-700/80 font-mono text-[10px] transition"
                                    >
                                      {peerTk}
                                    </button>
                                  ))}
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>

                      {/* Collapsible Excluded Non-Material Segments */}
                      {selectedEquityExcluded.length > 0 && (
                        <div className="pt-1">
                          <button
                            type="button"
                            onClick={() => setShowExcluded((prev) => !prev)}
                            className="text-xs font-mono text-slate-400 hover:text-slate-200 flex items-center gap-1.5"
                          >
                            <span>{showExcluded ? "▾" : "▸"}</span>
                            <span>
                              Excluded Non-Material / Divested Segments (
                              {selectedEquityExcluded.length})
                            </span>
                          </button>
                          {showExcluded && (
                            <div className="mt-2 rounded-xl bg-[#111827]/70 border border-slate-800/80 p-3.5 space-y-1.5">
                              {selectedEquityExcluded.map((ex, i) => (
                                <div
                                  key={`${ex.name}-${i}`}
                                  className="text-xs text-slate-400"
                                >
                                  <span className="text-slate-300 font-medium">
                                    {ex.name}:
                                  </span>{" "}
                                  {ex.reason}
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                    </>
                  )}
                </div>
              )}

              {/* TAB 2: VALUE CHAIN & PEERS */}
              {dossierTab === "value_chain" &&
                (totalValueChainCount === 0 ? (
                  <p className="text-xs text-slate-500">
                    No value-chain counterparties or sub-sector peers mapped for this company yet.
                  </p>
                ) : (
                  <div className="space-y-4">
                    {/* 1. Upstream Suppliers */}
                    <div className="rounded-xl bg-[#111827] border border-slate-800 p-4 space-y-3">
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
                              className="rounded-lg bg-[#090d16] border border-slate-800/80 p-3 space-y-1.5"
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
                                      ({sup.source_filing_type}{" "}
                                      {sup.source_filing_date})
                                    </span>
                                  )}
                                </p>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>

                    {/* 2. Competitors & Tracked Sub-Sector Peers */}
                    <div className="rounded-xl bg-[#111827] border border-slate-800 p-4 space-y-3">
                      <div className="text-[11px] font-mono uppercase tracking-wider text-purple-400">
                        2. Competitors &amp; Sub-Sector Peers ({filingCompetitors.length} Filing-Disclosed • {universePeers.length} Universe Peers)
                      </div>

                      {filingCompetitors.length > 0 && (
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
                          {filingCompetitors.map((comp, idx) => (
                            <div
                              key={`${comp.counterparty_name}-${idx}`}
                              className="rounded-lg bg-[#090d16] border border-slate-800/80 p-3 space-y-1.5"
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
                                      ({comp.source_filing_type}{" "}
                                      {comp.source_filing_date})
                                    </span>
                                  )}
                                </p>
                              )}
                            </div>
                          ))}
                        </div>
                      )}

                      {universePeers.length > 0 ? (
                        <div
                          className={
                            filingCompetitors.length > 0
                              ? "pt-2.5 border-t border-slate-800/70"
                              : ""
                          }
                        >
                          <div className="text-[10px] font-mono uppercase text-slate-500 mb-2">
                            Tracked Peers in {selectedEquity.sub_sector} (Click to Inspect):
                          </div>
                          <div className="flex flex-wrap gap-1.5">
                            {universePeers.map((peer) => (
                              <button
                                key={peer.ticker}
                                onClick={() => setSelectedTicker(peer.ticker)}
                                className="px-2.5 py-1 rounded-lg bg-[#090d16] hover:bg-slate-800 border border-slate-700/80 text-xs text-left transition flex items-center gap-1.5"
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
                    <div className="rounded-xl bg-[#111827] border border-slate-800 p-4 space-y-3">
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
                              className="rounded-lg bg-[#090d16] border border-slate-800/80 p-3 space-y-1.5"
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
                                      ({cust.source_filing_type}{" "}
                                      {cust.source_filing_date})
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
                ))}

              {/* TAB 3: COMMODITY EXPOSURE (Separated: Physical Commodities vs. Demand Drivers) */}
              {dossierTab === "commodities" &&
                (totalCommodityCount === 0 ? (
                  <p className="text-xs text-slate-500">
                    No direct commodity rules mapped for this sub-sector yet.
                  </p>
                ) : selectedEquityDrivers.length > 0 ? (
                  <div className="space-y-4">
                    {/* 1. Physical Commodities & Raw Materials */}
                    <div className="rounded-xl bg-[#111827] border border-slate-800 p-4 space-y-3">
                      <div className="text-[11px] font-mono uppercase tracking-wider text-amber-400">
                        1. Commodities &amp; Raw Materials ({dossierAiCommodities.length})
                      </div>
                      {dossierAiCommodities.length === 0 ? (
                        <p className="text-xs text-slate-500">
                          No physical commodities or raw material inputs extracted yet.
                        </p>
                      ) : (
                        <div className="space-y-2.5">
                          {dossierAiCommodities.map((d, idx) => (
                            <div
                              key={`${d.commodity_name}-${idx}`}
                              className="rounded-lg bg-[#090d16] border border-slate-800/80 p-3.5 space-y-2"
                            >
                              <div className="flex items-center justify-between gap-2 flex-wrap">
                                <div className="flex items-center gap-2 flex-wrap">
                                  <span className="font-semibold text-white text-xs">
                                    {d.commodity_name}
                                  </span>
                                  <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-300 text-[10px] font-mono">
                                    {d.exposure_role}
                                  </span>
                                  {d.matched_input_code &&
                                    validRegistryCodes.has(
                                      d.matched_input_code
                                    ) && (
                                      <button
                                        type="button"
                                        onClick={() =>
                                          handleSelectCommodityFromDossier(
                                            d.matched_input_code!
                                          )
                                        }
                                        title="Filter screener by this commodity feed"
                                        className="px-2 py-0.5 rounded bg-amber-500/15 hover:bg-amber-500/25 text-amber-300 border border-amber-500/30 text-[10px] font-mono transition"
                                      >
                                        {getCommodityDisplayName(
                                          d.matched_input_code
                                        )}{" "}
                                        ↗
                                      </button>
                                    )}
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
                                <p className="text-[11px] text-slate-400 italic leading-snug border-l-2 border-slate-700 pl-2.5">
                                  &ldquo;{d.source_quote}&rdquo;
                                  {d.source_filing_type && (
                                    <span className="not-italic font-mono text-[10px] text-slate-500 ml-1">
                                      ({d.source_filing_type}{" "}
                                      {d.source_filing_date})
                                    </span>
                                  )}
                                </p>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>

                    {/* 2. End-Market & Macro Demand Drivers */}
                    <div className="rounded-xl bg-[#111827] border border-slate-800 p-4 space-y-3">
                      <div className="text-[11px] font-mono uppercase tracking-wider text-sky-400">
                        2. End-Market &amp; Macro Demand Drivers ({dossierAiDemandDrivers.length})
                      </div>
                      {dossierAiDemandDrivers.length === 0 ? (
                        <p className="text-xs text-slate-500">
                          No macro or end-market demand drivers extracted yet.
                        </p>
                      ) : (
                        <div className="space-y-2.5">
                          {dossierAiDemandDrivers.map((d, idx) => (
                            <div
                              key={`${d.commodity_name}-${idx}`}
                              className="rounded-lg bg-[#090d16] border border-slate-800/80 p-3.5 space-y-2"
                            >
                              <div className="flex items-center justify-between gap-2 flex-wrap">
                                <div className="flex items-center gap-2 flex-wrap">
                                  <span className="font-semibold text-white text-xs">
                                    {formatShortDemandProxy(
                                      d.commodity_name,
                                      d.matched_input_code
                                    )}
                                  </span>
                                  <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-300 text-[10px] font-mono">
                                    {d.exposure_role}
                                  </span>
                                  {d.matched_input_code &&
                                    validRegistryCodes.has(
                                      d.matched_input_code
                                    ) && (
                                      <button
                                        type="button"
                                        onClick={() =>
                                          handleSelectCommodityFromDossier(
                                            d.matched_input_code!
                                          )
                                        }
                                        title="Filter screener by this macro demand driver"
                                        className="px-2 py-0.5 rounded bg-sky-500/15 hover:bg-sky-500/25 text-sky-300 border border-sky-500/30 text-[10px] font-mono transition"
                                      >
                                        Filter Proxy ↗
                                      </button>
                                    )}
                                  {d.cogs_or_rev_share_pct != null && (
                                    <span className="px-2 py-0.5 rounded bg-sky-500/15 text-sky-300 border border-sky-500/30 text-[10px] font-mono">
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
                                <p className="text-[11px] text-slate-400 italic leading-snug border-l-2 border-slate-700 pl-2.5">
                                  &ldquo;{d.source_quote}&rdquo;
                                  {d.source_filing_type && (
                                    <span className="not-italic font-mono text-[10px] text-slate-500 ml-1">
                                      ({d.source_filing_type}{" "}
                                      {d.source_filing_date})
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
                ) : (
                  <div className="space-y-4">
                    {/* 1. Sub-Sector Commodities & Raw Materials */}
                    <div className="rounded-xl bg-[#111827] border border-slate-800 p-4 space-y-3">
                      <div className="text-[11px] font-mono uppercase tracking-wider text-amber-400">
                        1. Commodities &amp; Raw Materials ({dossierSectorCommodities.length})
                      </div>
                      {dossierSectorCommodities.length === 0 ? (
                        <p className="text-xs text-slate-500">
                          No direct physical commodity rules mapped for this sub-sector.
                        </p>
                      ) : (
                        <div className="space-y-2.5">
                          {dossierSectorCommodities.map((s) => (
                            <div
                              key={s.input_code}
                              className="rounded-lg bg-[#090d16] border border-slate-800/80 p-3.5 space-y-1.5"
                            >
                              <div className="flex items-center justify-between gap-2 flex-wrap">
                                <button
                                  type="button"
                                  onClick={() =>
                                    handleSelectCommodityFromDossier(
                                      s.input_code
                                    )
                                  }
                                  className="font-semibold text-amber-300 hover:underline text-xs text-left"
                                >
                                  {getCommodityDisplayName(s.input_code)} —{" "}
                                  {s.relationship_type} ↗
                                </button>
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
                              <p className="text-xs text-slate-300 leading-relaxed">
                                {s.mechanism_note}
                              </p>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>

                    {/* 2. Sub-Sector End-Market & Macro Demand Drivers */}
                    <div className="rounded-xl bg-[#111827] border border-slate-800 p-4 space-y-3">
                      <div className="text-[11px] font-mono uppercase tracking-wider text-sky-400">
                        2. End-Market &amp; Macro Demand Drivers ({dossierSectorDemandDrivers.length})
                      </div>
                      {dossierSectorDemandDrivers.length === 0 ? (
                        <p className="text-xs text-slate-500">
                          No direct demand drivers mapped for this sub-sector.
                        </p>
                      ) : (
                        <div className="space-y-2.5">
                          {dossierSectorDemandDrivers.map((s) => (
                            <div
                              key={s.input_code}
                              className="rounded-lg bg-[#090d16] border border-slate-800/80 p-3.5 space-y-1.5"
                            >
                              <div className="flex items-center justify-between gap-2 flex-wrap">
                                <button
                                  type="button"
                                  onClick={() =>
                                    handleSelectCommodityFromDossier(
                                      s.input_code
                                    )
                                  }
                                  className="font-semibold text-sky-400 hover:underline text-xs text-left"
                                >
                                  {formatShortDemandProxy(
                                    getCommodityDisplayName(s.input_code),
                                    s.input_code
                                  )}{" "}
                                  — {s.relationship_type} ↗
                                </button>
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
                              <p className="text-xs text-slate-300 leading-relaxed">
                                {s.mechanism_note}
                              </p>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                ))}

{/* TAB 4: FILINGS & BRIEFS */}
              {dossierTab === "filings" && (
                <div className="space-y-4">
                  {selectedEquityFilings.length === 0 ? (
                    <div className="rounded-xl bg-[#111827] border border-slate-800 p-5 text-xs text-slate-400">
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
                              View Filing ↗
                            </a>
                          )}
                        </div>

                        <h4 className="text-sm font-semibold text-white">
                          {f.headline}
                        </h4>
                        <p className="text-xs text-slate-300 leading-relaxed">
                          {f.executive_summary}
                        </p>

                        <div className="grid grid-cols-1 gap-2 pt-1 text-xs">
                          {f.pricing_and_margins && (
                            <div className="rounded-lg bg-[#090d16] p-3 border border-slate-800/80">
                              <span className="font-mono text-[10px] uppercase text-emerald-400 block mb-0.5">
                                Pricing Power &amp; Margins
                              </span>
                              <span className="text-slate-300">
                                {f.pricing_and_margins}
                              </span>
                            </div>
                          )}
                          {f.capex_and_backlog && (
                            <div className="rounded-lg bg-[#090d16] p-3 border border-slate-800/80">
                              <span className="font-mono text-[10px] uppercase text-sky-400 block mb-0.5">
                                Backlog, Qualification &amp; Capex
                              </span>
                              <span className="text-slate-300">
                                {f.capex_and_backlog}
                              </span>
                            </div>
                          )}
                          {f.key_risks && (
                            <div className="rounded-lg bg-[#090d16] p-3 border border-slate-800/80">
                              <span className="font-mono text-[10px] uppercase text-rose-400 block mb-0.5">
                                Key Risks
                              </span>
                              <span className="text-slate-300">
                                {f.key_risks}
                              </span>
                            </div>
                          )}
                        </div>
                      </div>
                    ))
                  )}
                </div>
              )}

              {/* TAB 5: KEY METRICS (Quantitative Filing Table) */}
              {dossierTab === "metrics" && (
                <div className="space-y-4">
                  {!selectedEquityMetrics ? (
                    <div className="rounded-xl bg-[#111827] border border-slate-800 p-5 text-xs text-slate-400">
                      Quantitative filing metrics have not been extracted for{" "}
                      <span className="text-white font-mono">
                        {selectedEquity.ticker}
                      </span>{" "}
                      yet. Run the{" "}
                      <span className="text-sky-400 font-mono">
                        Extract Quantitative Filing Metrics
                      </span>{" "}
                      workflow to populate SEC XBRL / financial statement figures.
                    </div>
                  ) : (
                    <div className="rounded-xl bg-[#111827] border border-slate-800 overflow-hidden">
                      <div className="px-4 py-3 bg-[#0d1424] border-b border-slate-800/90 flex items-center justify-between gap-2 flex-wrap">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-[11px] font-mono uppercase tracking-wider text-sky-400 font-semibold">
                            Quantitative Filing Snapshot
                          </span>
                          <span className="px-2 py-0.5 rounded bg-sky-500/15 text-sky-300 border border-sky-500/30 text-[10px] font-mono">
                            {selectedEquityMetrics.fiscal_period}
                          </span>
                          <span className="text-[10px] font-mono text-slate-400">
                            Period End: {selectedEquityMetrics.period_end_date} •{" "}
                            {selectedEquityMetrics.currency}
                          </span>
                        </div>
                        {selectedEquityMetrics.source_url && (
                          <a
                            href={selectedEquityMetrics.source_url}
                            target="_blank"
                            rel="noreferrer"
                            className="text-[11px] font-mono text-sky-400 hover:underline"
                          >
                            {selectedEquityMetrics.source_type} ↗
                          </a>
                        )}
                      </div>

                      <div className="overflow-x-auto">
                        <table className="w-full text-left border-collapse text-xs">
                          <thead>
                            <tr className="border-b border-slate-800/80 text-[10px] font-mono uppercase text-slate-400 bg-[#090d16]/70">
                              <th className="py-2.5 px-4">Filing Metric</th>
                              <th className="py-2.5 px-4 text-right">
                                Reported Value
                              </th>
                              <th className="py-2.5 px-4 text-right">
                                Margin / Intensity / YoY
                              </th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-800/60">
                            {/* Revenue */}
                            <tr>
                              <td className="py-2.5 px-4 font-medium text-white">
                                Revenue
                              </td>
                              <td className="py-2.5 px-4 text-right font-mono text-white">
                                {formatFilingAmount(
                                  selectedEquityMetrics.revenue,
                                  selectedEquityMetrics.currency
                                )}
                              </td>
                              <td className="py-2.5 px-4 text-right font-mono">
                                {selectedEquityMetrics.revenue_yoy_pct != null ? (
                                  <span
                                    className={
                                      Number(
                                        selectedEquityMetrics.revenue_yoy_pct
                                      ) >= 0
                                        ? "text-emerald-400"
                                        : "text-rose-400"
                                    }
                                  >
                                    {formatSignedPct(
                                      selectedEquityMetrics.revenue_yoy_pct
                                    )}
                                  </span>
                                ) : (
                                  <span className="text-slate-500">—</span>
                                )}
                              </td>
                            </tr>

                            {/* Gross Profit & Margin */}
                            <tr>
                              <td className="py-2.5 px-4 font-medium text-slate-200">
                                Gross Profit &amp; Margin
                              </td>
                              <td className="py-2.5 px-4 text-right font-mono text-slate-200">
                                {formatFilingAmount(
                                  selectedEquityMetrics.gross_profit,
                                  selectedEquityMetrics.currency
                                )}
                              </td>
                              <td className="py-2.5 px-4 text-right font-mono">
                                <span className="text-white font-semibold">
                                  {formatMarginPct(
                                    selectedEquityMetrics.gross_margin_pct
                                  )}
                                </span>
                                {selectedEquityMetrics.gross_margin_yoy_bps !=
                                  null && (
                                  <span
                                    className={`ml-2 text-[10px] ${
                                      selectedEquityMetrics.gross_margin_yoy_bps >=
                                      0
                                        ? "text-emerald-400"
                                        : "text-rose-400"
                                    }`}
                                  >
                                    (
                                    {selectedEquityMetrics.gross_margin_yoy_bps >=
                                    0
                                      ? "+"
                                      : ""}
                                    {selectedEquityMetrics.gross_margin_yoy_bps}{" "}
                                    bps YoY)
                                  </span>
                                )}
                              </td>
                            </tr>

                            {/* Operating Income (EBIT) */}
                            <tr>
                              <td className="py-2.5 px-4 font-medium text-slate-200">
                                Operating Income (EBIT)
                              </td>
                              <td className="py-2.5 px-4 text-right font-mono text-slate-200">
                                {formatFilingAmount(
                                  selectedEquityMetrics.operating_income,
                                  selectedEquityMetrics.currency
                                )}
                              </td>
                              <td className="py-2.5 px-4 text-right font-mono text-slate-300">
                                {selectedEquityMetrics.operating_margin_pct !=
                                null
                                  ? `${formatMarginPct(
                                      selectedEquityMetrics.operating_margin_pct
                                    )} Op. Margin`
                                  : "—"}
                              </td>
                            </tr>

                            {/* Net Income */}
                            <tr>
                              <td className="py-2.5 px-4 font-medium text-slate-200">
                                Net Income
                              </td>
                              <td className="py-2.5 px-4 text-right font-mono text-slate-200">
                                {formatFilingAmount(
                                  selectedEquityMetrics.net_income,
                                  selectedEquityMetrics.currency
                                )}
                              </td>
                              <td className="py-2.5 px-4 text-right font-mono text-slate-300">
                                {selectedEquityMetrics.net_income != null &&
                                selectedEquityMetrics.revenue != null &&
                                Number(selectedEquityMetrics.revenue) !== 0
                                  ? `${(
                                      (Number(
                                        selectedEquityMetrics.net_income
                                      ) /
                                        Number(selectedEquityMetrics.revenue)) *
                                      100
                                    ).toFixed(1)}% Net Margin`
                                  : "Bottom-line GAAP/IFRS"}
                              </td>
                            </tr>

                            {/* Operating Cash Flow */}
                            <tr>
                              <td className="py-2.5 px-4 font-medium text-slate-200">
                                Operating Cash Flow
                              </td>
                              <td className="py-2.5 px-4 text-right font-mono text-slate-200">
                                {formatFilingAmount(
                                  selectedEquityMetrics.operating_cash_flow,
                                  selectedEquityMetrics.currency
                                )}
                              </td>
                              <td className="py-2.5 px-4 text-right font-mono text-slate-300">
                                {selectedEquityMetrics.operating_cash_flow !=
                                  null &&
                                selectedEquityMetrics.revenue != null &&
                                Number(selectedEquityMetrics.revenue) !== 0
                                  ? `${(
                                      (Number(
                                        selectedEquityMetrics
                                          .operating_cash_flow
                                      ) /
                                        Number(selectedEquityMetrics.revenue)) *
                                      100
                                    ).toFixed(1)}% OCF Margin`
                                  : "Cash from operations"}
                              </td>
                            </tr>

                            {/* Capital Expenditures */}
                            <tr>
                              <td className="py-2.5 px-4 font-medium text-slate-200">
                                Capital Expenditures (CapEx)
                              </td>
                              <td className="py-2.5 px-4 text-right font-mono text-slate-200">
                                {formatFilingAmount(
                                  selectedEquityMetrics.capex,
                                  selectedEquityMetrics.currency
                                )}
                              </td>
                              <td className="py-2.5 px-4 text-right font-mono text-amber-300">
                                {selectedEquityMetrics.capex_pct_rev != null
                                  ? `${formatMarginPct(
                                      selectedEquityMetrics.capex_pct_rev
                                    )} of Rev`
                                  : "—"}
                              </td>
                            </tr>

                            {/* Free Cash Flow */}
                            <tr>
                              <td className="py-2.5 px-4 font-medium text-white">
                                Free Cash Flow (FCF)
                              </td>
                              <td
                                className={`py-2.5 px-4 text-right font-mono font-semibold ${
                                  selectedEquityMetrics.free_cash_flow !=
                                    null &&
                                  Number(
                                    selectedEquityMetrics.free_cash_flow
                                  ) >= 0
                                    ? "text-emerald-400"
                                    : "text-rose-400"
                                }`}
                              >
                                {formatFilingAmount(
                                  selectedEquityMetrics.free_cash_flow,
                                  selectedEquityMetrics.currency
                                )}
                              </td>
                              <td className="py-2.5 px-4 text-right font-mono text-slate-300">
                                {selectedEquityMetrics.free_cash_flow != null &&
                                selectedEquityMetrics.revenue != null &&
                                Number(selectedEquityMetrics.revenue) !== 0
                                  ? `${(
                                      (Number(
                                        selectedEquityMetrics.free_cash_flow
                                      ) /
                                        Number(selectedEquityMetrics.revenue)) *
                                      100
                                    ).toFixed(1)}% FCF Margin`
                                  : "OCF minus CapEx"}
                              </td>
                            </tr>

                            {/* R&D Expense */}
                            {selectedEquityMetrics.rd_expense != null && (
                              <tr>
                                <td className="py-2.5 px-4 font-medium text-slate-200">
                                  R&amp;D Expense
                                </td>
                                <td className="py-2.5 px-4 text-right font-mono text-slate-200">
                                  {formatFilingAmount(
                                    selectedEquityMetrics.rd_expense,
                                    selectedEquityMetrics.currency
                                  )}
                                </td>
                                <td className="py-2.5 px-4 text-right font-mono text-sky-300">
                                  {selectedEquityMetrics.rd_pct_rev != null
                                    ? `${formatMarginPct(
                                        selectedEquityMetrics.rd_pct_rev
                                      )} of Rev`
                                    : "—"}
                                </td>
                              </tr>
                            )}

                            {/* Inventory & Turnover */}
                            <tr>
                              <td className="py-2.5 px-4 font-medium text-slate-200">
                                Inventory &amp; Turnover
                              </td>
                              <td className="py-2.5 px-4 text-right font-mono text-slate-200">
                                {formatFilingAmount(
                                  selectedEquityMetrics.inventory,
                                  selectedEquityMetrics.currency
                                )}
                              </td>
                              <td className="py-2.5 px-4 text-right font-mono text-slate-300">
                                {selectedEquityMetrics.inventory_days != null
                                  ? `${selectedEquityMetrics.inventory_days} Days (DIO)`
                                  : "—"}
                              </td>
                            </tr>

                            {/* Cash / Total Debt (Net Debt) */}
                            <tr>
                              <td className="py-2.5 px-4 font-medium text-slate-200">
                                Cash / Total Debt (Net Debt)
                              </td>
                              <td className="py-2.5 px-4 text-right font-mono text-slate-200">
                                {formatFilingAmount(
                                  selectedEquityMetrics.cash_and_equivalents,
                                  selectedEquityMetrics.currency
                                )}{" "}
                                /{" "}
                                {formatFilingAmount(
                                  selectedEquityMetrics.total_debt,
                                  selectedEquityMetrics.currency
                                )}
                              </td>
                              <td className="py-2.5 px-4 text-right font-mono text-slate-300">
                                Net:{" "}
                                {formatFilingAmount(
                                  selectedEquityMetrics.net_debt,
                                  selectedEquityMetrics.currency
                                )}
                              </td>
                            </tr>
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                </div>
              )}

                  {selectedEquityFilings.length === 0 ? (
                    <div className="rounded-xl bg-[#111827] border border-slate-800 p-5 text-xs text-slate-400">
                      {selectedEquityMetrics
                        ? "Narrative filing briefs are queued for the next SEC EDGAR / AI synthesis batch."
                        : "This ticker is queued for the next SEC EDGAR / AI synthesis batch."}
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
                              View Filing ↗
                            </a>
                          )}
                        </div>

                        <h4 className="text-sm font-semibold text-white">
                          {f.headline}
                        </h4>
                        <p className="text-xs text-slate-300 leading-relaxed">
                          {f.executive_summary}
                        </p>

                        <div className="grid grid-cols-1 gap-2 pt-1 text-xs">
                          {f.pricing_and_margins && (
                            <div className="rounded-lg bg-[#090d16] p-3 border border-slate-800/80">
                              <span className="font-mono text-[10px] uppercase text-emerald-400 block mb-0.5">
                                Pricing Power &amp; Margins
                              </span>
                              <span className="text-slate-300">
                                {f.pricing_and_margins}
                              </span>
                            </div>
                          )}
                          {f.capex_and_backlog && (
                            <div className="rounded-lg bg-[#090d16] p-3 border border-slate-800/80">
                              <span className="font-mono text-[10px] uppercase text-sky-400 block mb-0.5">
                                Backlog, Qualification &amp; Capex
                              </span>
                              <span className="text-slate-300">
                                {f.capex_and_backlog}
                              </span>
                            </div>
                          )}
                          {f.key_risks && (
                            <div className="rounded-lg bg-[#090d16] p-3 border border-slate-800/80">
                              <span className="font-mono text-[10px] uppercase text-rose-400 block mb-0.5">
                                Key Risks
                              </span>
                              <span className="text-slate-300">
                                {f.key_risks}
                              </span>
                            </div>
                          )}
                        </div>
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
