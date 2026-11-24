"use client";

import { useState, useMemo } from "react";

export interface ScopedInsightRow {
  scope_type: "industry" | "sub_industry" | "theme" | string;
  scope_name: string;
  generated_date: string;
  filing_count: number;
  sentiment: string;
  headline: string;
  key_drivers: string;
  pricing_and_margins: string;
  capex_and_supply_chain: string;
  top_tickers: string[];
}

const SENTIMENT_STYLES: Record<string, string> = {
  Bullish: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  Mixed: "bg-sky-500/15 text-sky-300 border-sky-500/30",
  Cautious: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  Bearish: "bg-rose-500/15 text-rose-300 border-rose-500/30",
};

export default function ScopedInsightsPanel({
  insights,
}: {
  insights: ScopedInsightRow[];
}) {
  const [scopeFilter, setScopeFilter] = useState<
    "ALL" | "industry" | "sub_industry" | "theme"
  >("ALL");

  const filtered = useMemo(() => {
    if (!insights) return [];
    if (scopeFilter === "ALL") return insights;
    return insights.filter((item) => item.scope_type === scopeFilter);
  }, [insights, scopeFilter]);

  if (!insights || insights.length === 0) {
    return null;
  }

  return (
    <section className="max-w-7xl mx-auto px-6 pb-12 space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-t border-slate-800/80 pt-8">
        <div>
          <span className="px-2 py-0.5 rounded text-[10px] font-mono uppercase tracking-wider bg-sky-500/15 text-sky-400 border border-sky-500/30">
            AI Sector &amp; Theme Synthesis
          </span>
          <h2 className="text-base font-semibold text-white mt-1.5">
            Industry, Sub-Sector &amp; Secular Theme Breakdown
          </h2>
        </div>

        <div className="flex flex-wrap gap-1.5">
          {[
            { id: "ALL", label: `All (${insights.length})` },
            { id: "industry", label: "Industry" },
            { id: "sub_industry", label: "Sub-Sectors" },
            { id: "theme", label: "Themes" },
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setScopeFilter(tab.id as typeof scopeFilter)}
              className={`px-3 py-1 rounded-md text-xs font-medium transition ${
                scopeFilter === tab.id
                  ? "bg-sky-500 text-white"
                  : "bg-slate-800/80 text-slate-300 hover:bg-slate-800"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {filtered.map((item) => {
          const badgeStyle =
            SENTIMENT_STYLES[item.sentiment] ||
            "bg-slate-800 text-slate-300 border-slate-700";

          return (
            <div
              key={`${item.scope_type}-${item.scope_name}`}
              className="rounded-xl border border-slate-800 bg-[#111827]/90 p-5 space-y-3"
            >
              <div className="flex items-start justify-between gap-2">
                <div>
                  <span className="text-[10px] font-mono uppercase tracking-wider text-slate-400">
                    {item.scope_type.replace("_", " ")} • {item.filing_count} Filings
                  </span>
                  <h3 className="text-sm font-semibold text-white mt-0.5">
                    {item.scope_name}
                  </h3>
                </div>
                <span
                  className={`px-2.5 py-0.5 rounded-full text-[11px] font-mono border shrink-0 ${badgeStyle}`}
                >
                  {item.sentiment}
                </span>
              </div>

              <p className="text-xs font-medium text-sky-300 leading-snug">
                {item.headline}
              </p>

              <div className="space-y-2 text-xs text-slate-300 leading-relaxed">
                <p>
                  <strong className="text-slate-200">Demand &amp; Drivers:</strong>{" "}
                  {item.key_drivers}
                </p>
                <p>
                  <strong className="text-slate-200">Pricing &amp; Margins:</strong>{" "}
                  {item.pricing_and_margins}
                </p>
                <p>
                  <strong className="text-slate-200">Capex &amp; Supply Chain:</strong>{" "}
                  {item.capex_and_supply_chain}
                </p>
              </div>

              {item.top_tickers && item.top_tickers.length > 0 && (
                <div className="flex items-center flex-wrap gap-1.5 pt-1 border-t border-slate-800/70">
                  <span className="text-[10px] font-mono uppercase text-slate-500 mr-1">
                    Key Tickers:
                  </span>
                  {item.top_tickers.map((tk) => (
                    <span
                      key={tk}
                      className="px-2 py-0.5 rounded bg-[#090d16] border border-slate-800 text-[11px] font-mono text-sky-400"
                    >
                      {tk}
                    </span>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
