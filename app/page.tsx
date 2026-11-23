export const instant = false;

import { connection } from "next/server";
import { neon } from "@neondatabase/serverless";
import DashboardClient, {
  EquityRow,
  RawInputRow,
  SensitivityRow,
  FilingRow,
  MacroNarrativeRow,
} from "./DashboardClient";
import ScopedInsightsPanel, {
  ScopedInsight,
} from "@/components/ScopedInsightsPanel";

export default async function HomePage() {
  await connection();
  const sql = neon(process.env.DATABASE_URL!);

  const equities = (await sql`
    SELECT ticker, company_name, sub_sector, contract_type, qualification_years, adv_mat_rev_pct,
      country
    FROM equities_universe
    ORDER BY qualification_years DESC NULLS LAST, ticker ASC
  `) as EquityRow[];

  const rawInputs = (await sql`
    SELECT DISTINCT ON (input_code)
      input_code,
      input_name,
      category,
      unit,
      observation_date::text AS observation_date,
      value
    FROM raw_inputs
    ORDER BY input_code, observation_date DESC
  `) as RawInputRow[];

  const sensitivities = (await sql`
    SELECT sub_sector, input_code, relationship_type, impact_direction, mechanism_note
    FROM commodity_sensitivity
  `) as SensitivityRow[];

  const filings = (await sql`
    SELECT
      f.id,
      f.ticker,
      e.company_name,
      e.sub_sector,
      f.filing_type,
      f.filing_date::text AS filing_date,
      f.headline,
      f.executive_summary,
      f.theme_tags,
      f.pricing_and_margins,
      f.capex_and_backlog,
      f.key_risks,
      f.source_url
    FROM company_filings_ai f
    LEFT JOIN equities_universe e ON f.ticker = e.ticker
    ORDER BY f.filing_date DESC, f.id DESC
    LIMIT 250
  `) as FilingRow[];

  const narratives = (await sql`
    SELECT
      generated_date::text AS generated_date,
      overall_regime,
      executive_brief,
      ai_theme_summary,
      aerospace_theme_summary,
      commodities_theme_summary,
      electrification_battery_summary
    FROM macro_narrative
    ORDER BY generated_date DESC
    LIMIT 1
  `) as MacroNarrativeRow[];

  let scopedInsights: ScopedInsight[] = [];
  try {
    scopedInsights = (await sql`
      SELECT
        scope_type,
        scope_name,
        generated_date::text AS generated_date,
        filing_count,
        sentiment,
        headline,
        key_drivers,
        pricing_and_margins,
        capex_and_supply_chain,
        top_tickers
      FROM sector_theme_insights
      ORDER BY scope_type ASC, filing_count DESC
    `) as ScopedInsight[];
  } catch {
    scopedInsights = [];
  }

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <DashboardClient
        equities={equities}
        inputs={rawInputs}
        sensitivities={sensitivities}
        filings={filings}
        narrative={narratives[0] || null}
      />
      <div className="mx-auto max-w-7xl px-4 pb-12 sm:px-6 lg:px-8">
        <ScopedInsightsPanel insights={scopedInsights} />
      </div>
    </div>
  );
}
