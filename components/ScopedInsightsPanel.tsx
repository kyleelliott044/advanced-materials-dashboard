"use client";

import { useState } from "react";

export type ScopedInsight = {
  scope_type: "industry" | "sub_industry" | "theme";
  scope_name: string;
  generated_date: string;
  filing_count: number;
  sentiment: string;
  headline: string;
  key_drivers: string;
  pricing_and_margins: string;
  capex_and_supply_chain: string;
  top_tickers: string[];
};

const SENTIMENT_STYLES: Record<string, string> = {
  Bullish: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  Mixed: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  Cautious: "bg-orange-500/15 text-orange-400 border-orange-500/30",
  Bearish: "bg-rose-500/15 text-rose-400 border-rose-500/30",
};

export default function ScopedInsightsPanel({
  insights,
}: {
  insights: ScopedInsight[];
}) {
  const [tab, setTab] = useState<"industry" | "sub_industry" | "theme">("industry");

  const filtered = insights.filter((item) => item.scope_type === tab);

  return (
    <section className="rounded-xl border border-zinc-800 bg-zinc-950/70 p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-zinc-100">
            SEC Filing Intelligence: Industry, Sub-Industry &amp; Themes
          </h2>
          <p className="text-xs text-zinc-400">
            Synthesized directly from parsed 10-K, 10-Q, and global earnings briefs
          </p>
        </div>

        <div className="flex rounded-lg border border-zinc-800 bg-zinc-900 p-1 text-xs">
          {[
            { id: "industry", label: "Industry" },
            { id: "sub_industry", label: "Sub-Industries" },
            { id: "theme", label: "Themes" },
          ].map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id as typeof tab)}
              className={`rounded-md px-3 py-1.5 font-medium transition ${
                tab === t.id
                  ? "bg-zinc-100 text-zinc-950"
                  : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {filtered.length === 0 ? (
        <p className="text-sm text-zinc-500">
          No insights generated for this tab yet. Run the GitHub Actions workflow to populate{" "}
          <code>sector_theme_insights</code>.
        </p>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {filtered.map((item) => (
            <div
              key={`${item.scope_type}-${item.scope_name}`}
              className="rounded-lg border border-zinc-800/80 bg-zinc-900/50 p-4"
            >
              <div className="mb-2 flex items-center justify-between gap-2">
                <h3 className="font-semibold text-zinc-100">{item.scope_name}</h3>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-zinc-500">
                    {item.filing_count} filings
                  </span>
                  <span
                    className={`rounded-full border px-2 py-0.5 text-xs font-medium ${
                      SENTIMENT_STYLES[item.sentiment] ??
                      "bg-zinc-800 text-zinc-300 border-zinc-700"
                    }`}
                  >
                    {item.sentiment}
                  </span>
                </div>
              </div>

              <p className="mb-3 text-sm font-medium text-zinc-200">{item.headline}</p>

              <div className="space-y-2 text-xs leading-relaxed text-zinc-400">
                <p>
                  <span className="font-semibold text-zinc-300">Drivers: </span>
                  {item.key_drivers}
                </p>
                <p>
                  <span className="font-semibold text-zinc-300">Pricing &amp; Margins: </span>
                  {item.pricing_and_margins}
                </p>
                <p>
                  <span className="font-semibold text-zinc-300">Capex &amp; Supply Chain: </span>
                  {item.capex_and_supply_chain}
                </p>
              </div>

              {item.top_tickers?.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {item.top_tickers.map((ticker) => (
                    <span
                      key={ticker}
                      className="rounded bg-zinc-800 px-2 py-0.5 font-mono text-[11px] text-zinc-300"
                    >
                      {ticker}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
