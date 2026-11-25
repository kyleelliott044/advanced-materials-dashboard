import { Suspense } from "react";
import { connection } from "next/server";
import { neon } from "@neondatabase/serverless";
import DashboardClient, {
  EquityRow,
  RawInputRow,
  SensitivityRow,
  FilingRow,
  MacroNarrativeRow,
  SupplyChainRow,
  CommodityDriverRow,
} from "./DashboardClient";
import ScopedInsightsPanel, { ScopedInsightRow } from "./ScopedInsightsPanel";
import {
  CompanyProduct,
  ExcludedProduct,
  CompanyProductProfile,
} from "../lib/productRollup";

async function DashboardDataLoader() {
  await connection();
  const sql = neon(process.env.DATABASE_URL!);

  const [
    rawEquities,
    inputs,
    sensitivities,
    filings,
    narratives,
    rawSupplyChain,
    rawDrivers,
    insights,
    rawProducts,
    rawExcluded,
    rawProfiles,
  ] = await Promise.all([
    sql`
      SELECT
        ticker,
        company_name,
        sub_sector,
        contract_type,
        qualification_years,
        adv_mat_rev_pct,
        country,
        market_cap_usd
      FROM equities_universe
      ORDER BY market_cap_usd DESC NULLS LAST, adv_mat_rev_pct DESC NULLS LAST, ticker ASC
    `,
    sql`
      SELECT DISTINCT ON (input_code)
        input_code,
        input_name,
        category,
        unit,
        observation_date::text AS observation_date,
        value,
        COALESCE(is_proxy, FALSE) AS is_proxy,
        source_note
      FROM raw_inputs
      ORDER BY input_code, observation_date DESC
    `,
    sql`
      SELECT
        sub_sector,
        input_code,
        relationship_type,
        impact_direction,
        mechanism_note
      FROM commodity_sensitivity
    `,
    sql`
      SELECT
        id,
        ticker,
        company_name,
        sub_sector,
        filing_type,
        filing_date,
        fiscal_period,
        headline,
        executive_summary,
        theme_tags,
        pricing_and_margins,
        capex_and_backlog,
        key_risks,
        source_url
      FROM (
        SELECT
          f.id,
          f.ticker,
          e.company_name,
          e.sub_sector,
          f.filing_type,
          f.filing_date::text AS filing_date,
          f.fiscal_period,
          f.headline,
          f.executive_summary,
          f.theme_tags,
          f.pricing_and_margins,
          f.capex_and_backlog,
          f.key_risks,
          f.source_url,
          ROW_NUMBER() OVER (
            PARTITION BY f.ticker
            ORDER BY f.filing_date DESC, f.id DESC
          ) AS rn
        FROM company_filings_ai f
        LEFT JOIN equities_universe e ON f.ticker = e.ticker
      ) ranked
      WHERE rn <= 3
      ORDER BY filing_date DESC, id DESC
    `,
    sql`
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
    `,
    sql`
      SELECT
        ticker,
        direction,
        counterparty_name,
        is_named_entity,
        material_or_product,
        is_sole_source,
        exposure_pct,
        source_filing_type,
        source_filing_date::text AS source_filing_date,
        source_quote
      FROM equity_supply_chain
      ORDER BY is_named_entity DESC, is_sole_source DESC, exposure_pct DESC NULLS LAST
    `,
    sql`
      SELECT
        ticker,
        commodity_name,
        matched_input_code,
        exposure_role,
        impact_direction,
        pass_through_lag,
        cogs_or_rev_share_pct,
        mechanism_detail,
        source_filing_type,
        source_filing_date::text AS source_filing_date,
        source_quote
      FROM equity_commodity_drivers
      ORDER BY cogs_or_rev_share_pct DESC NULLS LAST, commodity_name ASC
    `,
    sql`
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
      ORDER BY filing_count DESC, scope_name ASC
    `,
    sql`
      SELECT
        id, ticker, name, category, business_unit, status, as_of,
        advanced, raw_materials, downstream, pricing_structure,
        revenue, peers, src, conf
      FROM company_products
      ORDER BY ticker ASC, id ASC
    `.catch(() => []),
    sql`
      SELECT ticker, name, reason
      FROM company_excluded_products
      ORDER BY ticker ASC, id ASC
    `.catch(() => []),
    sql`
      SELECT ticker, note
      FROM company_product_profiles
    `.catch(() => []),
  ]);

  const equities: EquityRow[] = rawEquities.map((row) => ({
    ticker: row.ticker,
    company_name: row.company_name,
    sub_sector: row.sub_sector,
    contract_type: row.contract_type,
    qualification_years: row.qualification_years,
    adv_mat_rev_pct:
      row.adv_mat_rev_pct != null ? Number(row.adv_mat_rev_pct) : null,
    country: row.country,
    market_cap_usd:
      row.market_cap_usd != null ? Number(row.market_cap_usd) : null,
  }));

  const supplyChain: SupplyChainRow[] = rawSupplyChain.map((row) => ({
    ticker: row.ticker,
    direction: row.direction,
    counterparty_name: row.counterparty_name,
    is_named_entity: Boolean(row.is_named_entity),
    material_or_product: row.material_or_product,
    is_sole_source: Boolean(row.is_sole_source),
    exposure_pct: row.exposure_pct != null ? Number(row.exposure_pct) : null,
    source_filing_type: row.source_filing_type,
    source_filing_date: row.source_filing_date,
    source_quote: row.source_quote,
  }));

  const commodityDrivers: CommodityDriverRow[] = rawDrivers.map((row) => ({
    ticker: row.ticker,
    commodity_name: row.commodity_name,
    matched_input_code: row.matched_input_code,
    exposure_role: row.exposure_role,
    impact_direction: row.impact_direction,
    pass_through_lag: row.pass_through_lag,
    cogs_or_rev_share_pct:
      row.cogs_or_rev_share_pct != null
        ? Number(row.cogs_or_rev_share_pct)
        : null,
    mechanism_detail: row.mechanism_detail,
    source_filing_type: row.source_filing_type,
    source_filing_date: row.source_filing_date,
    source_quote: row.source_quote,
  }));

  const products: CompanyProduct[] = rawProducts.map((row: any) => ({
    id: row.id,
    ticker: row.ticker,
    name: row.name,
    category: row.category,
    business_unit: row.business_unit,
    status: row.status === "divested" ? "divested" : "active",
    as_of: row.as_of,
    advanced:
      typeof row.advanced === "string"
        ? JSON.parse(row.advanced)
        : row.advanced || {
            performance_spec: "",
            qualification_months: null,
            switching_barrier: "",
          },
    raw_materials: Array.isArray(row.raw_materials)
      ? row.raw_materials
      : typeof row.raw_materials === "string"
      ? JSON.parse(row.raw_materials)
      : [],
    downstream:
      typeof row.downstream === "string"
        ? JSON.parse(row.downstream)
        : row.downstream || {
            process_step: "",
            end_product: "",
            end_demand: "",
            demand_proxy_ref: null,
            customers: null,
          },
    pricing_structure: row.pricing_structure ?? null,
    revenue:
      typeof row.revenue === "string"
        ? JSON.parse(row.revenue)
        : row.revenue || { basis: "business_unit", share_pct: null },
    peers: Array.isArray(row.peers) ? row.peers : [],
    src:
      typeof row.src === "string"
        ? JSON.parse(row.src)
        : row.src || { type: "inferred", ref: null },
    conf: row.conf || "med",
  }));

  const narrative = (narratives[0] as MacroNarrativeRow) || null;

  return (
    <>
      <DashboardClient
        equities={equities}
        inputs={inputs as RawInputRow[]}
        sensitivities={sensitivities as SensitivityRow[]}
        filings={filings as FilingRow[]}
        narrative={narrative}
        supplyChain={supplyChain}
        commodityDrivers={commodityDrivers}
        products={products}
        excludedProducts={rawExcluded as ExcludedProduct[]}
        productProfiles={rawProfiles as CompanyProductProfile[]}
      />
      <ScopedInsightsPanel insights={insights as ScopedInsightRow[]} />
    </>
  );
}

export default function HomePage() {
  return (
    <div className="min-h-screen bg-[#090d16]">
      <Suspense
        fallback={
          <div className="max-w-7xl mx-auto px-6 py-12 text-xs font-mono text-slate-400">
            Loading Advanced Materials Intelligence Terminal...
          </div>
        }
      >
        <DashboardDataLoader />
      </Suspense>
    </div>
  );
}
