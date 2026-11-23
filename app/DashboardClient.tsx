"use client";

import { useState, useMemo, useEffect } from "react";

export interface EquityRow {
  ticker: string;
  company_name: string;
  sub_sector: string | null;
  contract_type: string | null;
  qualification_years: string | number | null;
  adv_mat_rev_pct: number | null;
  country: string | null;
}

export interface RawInputRow {
  input_code: string;
  input_name: string;
  category: string | null;
  unit: string | null;
  observation_date: string;
  value: string | number;
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

type SortOption =
  | "rev_desc"
  | "rev_asc"
  | "country_asc"
  | "moat_desc"
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

export default function DashboardClient({
  equities,
  inputs,
  sensitivities,
  filings,
  narrative,
}: {
  equities: EquityRow[];
  inputs: RawInputRow[];
  sensitivities: SensitivityRow[];
  filings: FilingRow[];
  narrative: MacroNarrativeRow | null;
}) {
  const [activeTab, setActiveTab] = useState<"screener" | "feed">("screener");
  const [narrativeTab, setNarrativeTab] = useState<
    "executive" | "ai" | "aero" | "commodities" | "ev"
  >("executive");
  const [search, setSearch] = useState("");
  const [sectorFilter, setSectorFilter] = useState("ALL");
  const [countryFilter, setCountryFilter] = useState("ALL");
  const [contractFilter, setContractFilter] = useState("ALL");
  const [sortBy, setSortBy] = useState<SortOption>("rev_desc");
  const [selectedCommodity, setSelectedCommodity] = useState<string | null>(null);
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

  // Map sub_sector -> sensitivity rules
  const sectorSensitivitiesMap = useMemo(() => {
    const map: Record<string, SensitivityRow[]> = {};
    for (const s of sensitivities) {
      if (!map[s.sub_sector]) map[s.sub_sector] = [];
      map[s.sub_sector].push(s);
    }
    return map;
  }, [sensitivities]);

  // Map ticker -> filings
  const tickerFilingsMap = useMemo(() => {
    const map: Record<string, FilingRow[]> = {};
    for (const f of filings) {
      if (!map[f.ticker]) map[f.ticker] = [];
      map[f.ticker].push(f);
    }
    return map;
  }, [filings]);

  const filteredEquities = useMemo(() => {
    const q = search.toLowerCase();
    const filtered = equities.filter((eq) => {
      const matchesSearch =
        eq.ticker.toLowerCase().includes(q) ||
        eq.company_name.toLowerCase().includes(q) ||
        (eq.sub_sector || "").toLowerCase().includes(q) ||
        (eq.country || "").toLowerCase().includes(q);
      const matchesSector =
        sectorFilter === "ALL" || eq.sub_sector === sectorFilter;
      const matchesCountry =
        countryFilter === "ALL" || eq.country === countryFilter;
      const matchesContract =
        contractFilter === "ALL" || eq.contract_type === contractFilter;
      const qual = Number(eq.qualification_years || 0);

      let matchesCommodity = true;
      if (selectedCommodity) {
        const rules = sectorSensitivitiesMap[eq.sub_sector || ""] || [];
        matchesCommodity = rules.some((r) => r.input_code === selectedCommodity);
      }

      return (
        matchesSearch &&
        matchesSector &&
        matchesCountry &&
        matchesContract &&
        matchesCommodity &&
        qual >= minQualYears
      );
    });

    return [...filtered].sort((a, b) => {
      if (sortBy === "rev_desc") {
        const diff = (b.adv_mat_rev_pct ?? -1) - (a.adv_mat_rev_pct ?? -1);
        return diff !== 0 ? diff : a.ticker.localeCompare(b.ticker);
      }
      if (sortBy === "rev_asc") {
        const diff = (a.adv_mat_rev_pct ?? 999) - (b.adv_mat_rev_pct ?? 999);
        return diff !== 0 ? diff : a.ticker.localeCompare(b.ticker);
      }
      if (sortBy === "country_asc") {
        const countryDiff = (a.country || "ZZZ").localeCompare(b.country || "ZZZ");
        if (countryDiff !== 0) return countryDiff;
        return (b.adv_mat_rev_pct ?? -1) - (a.adv_mat_rev_pct ?? -1);
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
    sectorSensitivitiesMap,
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

  const selectedEquityFilings = selectedTicker
    ? tickerFilingsMap[selectedTicker] || []
    : [];

  const selectedEquitySensitivities = selectedEquity?.sub_sector
    ? sectorSensitivitiesMap[selectedEquity.sub_sector] || []
    : [];

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
              {equities.length} Global Equities • {filings.length} AI-Parsed 10-K/10-Q &amp; Earnings Briefs • Commodity Sensitivity Engine
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
                Run the GitHub Actions workflow (`filings_ai.py`) with your `GEMINI_API_KEY` secret to generate the cross-filing macro narrative.
              </p>
            ) : (
              <>
                {narrativeTab === "executive" && <p>{narrative.executive_brief}</p>}
                {narrativeTab === "ai" && <p>{narrative.ai_theme_summary}</p>}
                {narrativeTab === "aero" && <p>{narrative.aerospace_theme_summary}</p>}
                {narrativeTab === "commodities" && <p>{narrative.commodities_theme_summary}</p>}
                {narrativeTab === "ev" && <p>{narrative.electrification_battery_summary}</p>}
                <div className="mt-3 text-[11px] font-mono text-slate-500">
                  Synthesized across {filings.length} company filings &amp; {inputs.length} commodity feeds • Updated {narrative.generated_date}
                </div>
              </>
            )}
          </div>
        </section>

        {/* Interactive Commodity & Macro Filter Cards */}
        <section>
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-3">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                Click Any Commodity / Macro Proxy to Filter Exposed Stocks
              </h2>
              {selectedCommodity && (
                <button
                  onClick={() => setSelectedCommodity(null)}
                  className="text-xs font-mono text-sky-400 hover:text-sky-300 underline"
                >
                  Clear Filter ({selectedCommodity})
                </button>
              )}
            </div>
            <span className="text-xs text-slate-500">EOD Market &amp; FRED Data</span>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-4 gap-3">
            {inputs.map((item) => {
              const isSelected = selectedCommodity === item.input_code;
              return (
                <button
                  key={item.input_code}
                  onClick={() =>
                    setSelectedCommodity(isSelected ? null : item.input_code)
                  }
                  className={`text-left rounded-xl p-3.5 border transition ${
                    isSelected
                      ? "bg-sky-950/50 border-sky-400 ring-1 ring-sky-400/50"
                      : "bg-[#111827]/90 border-slate-800/90 hover:border-slate-700"
                  }`}
                >
                  <div className="flex items-center justify-between text-[11px] font-mono">
                    <span className="text-sky-400 font-semibold">{item.input_code}</span>
                    <span className="text-slate-500 truncate max-w-[115px]">
                      {item.category}
                    </span>
                  </div>
                  <div className="mt-1 text-xs font-medium text-slate-200 truncate">
                    {item.input_name}
                  </div>
                  <div className="mt-2.5 flex items-baseline justify-between">
                    <span className="text-lg font-semibold font-mono text-white">
                      {Number(item.value).toLocaleString(undefined, {
                        minimumFractionDigits: 2,
                        maximumFractionDigits: 2,
                      })}
                    </span>
                    <span className="text-[10px] text-slate-500">{item.unit}</span>
                  </div>
                </button>
              );
            })}
          </div>
        </section>

        {/* TAB 1: Equity Universe & Commodity Exposure Table */}
        {activeTab === "screener" && (
          <section className="space-y-4">
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-6 gap-3 bg-[#111827]/80 border border-slate-800 rounded-xl p-4">
              <div className="sm:col-span-2 lg:col-span-2">
                <label className="block text-[11px] font-mono uppercase text-slate-400 mb-1.5">
                  Search Ticker, Company, Sub-Sector, or Country
                </label>
                <input
                  type="text"
                  placeholder="Search or click any row for 10-K dossier..."
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
                  Contract Type
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
                  <option value="rev_desc">Adv. Mat. Rev % (High → Low)</option>
                  <option value="rev_asc">Adv. Mat. Rev % (Low → High)</option>
                  <option value="country_asc">Country (A → Z)</option>
                  <option value="moat_desc">Qual. Moat (High → Low)</option>
                  <option value="ticker_asc">Ticker (A → Z)</option>
                </select>
              </div>

              <div className="sm:col-span-2 lg:col-span-6 flex items-center gap-4 pt-1 border-t border-slate-800/70">
                <label className="text-[11px] font-mono uppercase text-slate-400 whitespace-nowrap">
                  Min Moat: {minQualYears} yrs
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
                  search) && (
                  <button
                    onClick={() => {
                      setSearch("");
                      setCountryFilter("ALL");
                      setSectorFilter("ALL");
                      setContractFilter("ALL");
                      setMinQualYears(0);
                    }}
                    className="ml-auto text-xs font-mono text-sky-400 hover:text-sky-300 underline"
                  >
                    Reset Filters
                  </button>
                )}
              </div>
            </div>

            <div className="rounded-xl border border-slate-800 bg-[#111827]/70 overflow-hidden">
              <div className="px-5 py-3 border-b border-slate-800 flex items-center justify-between text-xs text-slate-400">
                <span>
                  Showing <strong className="text-white">{filteredEquities.length}</strong> of{" "}
                  {equities.length} companies{" "}
                  {selectedCommodity && (
                    <span className="text-sky-400 font-mono">
                      — Exposed to {selectedCommodity}
                    </span>
                  )}
                </span>
                <span className="text-sky-400 font-medium">
                  Click any company row for 10-K/Earnings Dossier &rarr;
                </span>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="border-b border-slate-800/90 text-[11px] font-mono uppercase text-slate-400 bg-[#0c1220]">
                      <th className="py-3.5 px-4">Ticker</th>
                      <th className="py-3.5 px-4">Company</th>
                      <th className="py-3.5 px-4">Country</th>
                      <th className="py-3.5 px-4 text-right">Adv. Mat. Rev</th>
                      <th className="py-3.5 px-4">Commodity Drivers</th>
                      <th className="py-3.5 px-4">Pricing Structure</th>
                      <th className="py-3.5 px-4">AI Filings</th>
                      <th className="py-3.5 px-4 text-right">Qual. Moat</th>
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
                      const revPct = eq.adv_mat_rev_pct;
                      const rules = sectorSensitivitiesMap[eq.sub_sector || ""] || [];
                      const eqFilings = tickerFilingsMap[eq.ticker] || [];

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
                          </td>
                          <td className="py-3.5 px-4 text-xs text-slate-300 whitespace-nowrap">
                            {eq.country || "—"}
                          </td>
                          <td className="py-3.5 px-4 text-right font-mono">
                            {revPct != null ? (
                              <span
                                className={`inline-block px-2 py-0.5 rounded text-xs font-semibold border ${
                                  revPct >= 80
                                    ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
                                    : revPct >= 40
                                    ? "bg-sky-500/15 text-sky-300 border-sky-500/30"
                                    : "bg-slate-800 text-slate-300 border-slate-700"
                                }`}
                              >
                                {revPct}%
                              </span>
                            ) : (
                              <span className="text-xs text-slate-600">—</span>
                            )}
                          </td>
                          <td className="py-3.5 px-4">
                            <div className="flex flex-wrap gap-1.5">
                              {rules.length === 0 ? (
                                <span className="text-xs text-slate-500">—</span>
                              ) : (
                                rules.map((r) => (
                                  <span
                                    key={r.input_code}
                                    className={`px-2 py-0.5 rounded text-[11px] font-mono border ${
                                      r.impact_direction.includes("Inverse")
                                        ? "bg-rose-500/10 text-rose-300 border-rose-500/30"
                                        : r.impact_direction.includes("Direct")
                                        ? "bg-emerald-500/10 text-emerald-300 border-emerald-500/30"
                                        : "bg-sky-500/10 text-sky-300 border-sky-500/30"
                                    }`}
                                    title={`${r.relationship_type}: ${r.mechanism_note}`}
                                  >
                                    {r.input_code}
                                  </span>
                                ))
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
                Latest AI-Parsed 10-K, 10-Q &amp; Global Earnings Summaries
              </h2>
              <span className="text-xs text-slate-400">
                Showing {filteredFilings.length} of {filings.length} briefs • Click any card for full dossier
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
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="font-mono font-bold text-sky-400 text-sm">
                          {f.ticker}
                        </span>
                        <span className="text-xs font-medium text-slate-300">
                          {f.company_name}
                        </span>
                      </div>
                      <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-300 text-[11px] font-mono">
                        {f.filing_type} • {f.filing_date}
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
          <div className="w-full max-w-2xl bg-[#0c1220] border-l border-slate-800 h-full overflow-y-auto p-6 space-y-6 shadow-2xl">
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
                  <span>Contract: {selectedEquity.contract_type}</span>
                  <span>•</span>
                  <span>Qual. Moat: {selectedEquity.qualification_years} yrs</span>
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

            {/* Commodity & Macro Sensitivity Breakdown */}
            <div className="space-y-2.5">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                Commodity &amp; Macro Exposure Mechanics
              </h3>
              {selectedEquitySensitivities.length === 0 ? (
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
                        <span className="font-mono font-semibold text-sky-400 text-xs">
                          {s.input_code} — {s.relationship_type}
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

            {/* Last Few 10-K / 10-Q / Earnings Reports */}
            <div className="space-y-3">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                Recent 10-K, 10-Q &amp; Earnings Intelligence ({selectedEquityFilings.length})
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
                    <div className="flex items-center justify-between">
                      <span className="px-2.5 py-0.5 rounded bg-sky-500/15 text-sky-300 border border-sky-500/30 text-xs font-mono font-semibold">
                        {f.filing_type} • Filed {f.filing_date}
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
