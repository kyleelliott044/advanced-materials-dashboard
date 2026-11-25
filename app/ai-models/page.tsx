import { Suspense } from "react";
import Link from "next/link";
import { connection } from "next/server";
import { neon } from "@neondatabase/serverless";

interface ModelSpec {
  id: string;
  name: string;
  provider: "Google Gemini" | "Groq";
  priorityRank: number;
  priorityTier: string;
  strengthScore: number; // out of 10
  speedLabel: string;
  contextWindow: string;
  defaultRpd: number;
  defaultTpd: number | null;
  summary: string;
}

const MODEL_REGISTRY: ModelSpec[] = [
  {
    id: "gemini-3.8-flash",
    name: "Gemini 3.8 Flash",
    provider: "Google Gemini",
    priorityRank: 1,
    priorityTier: "Primary (Slot #1)",
    strengthScore: 9.5,
    speedLabel: "Fast",
    contextWindow: "1M tokens",
    defaultRpd: 250,
    defaultTpd: null,
    summary:
      "First-choice engine for SEC 10-K/20-F parsing, product decomposition, and live Google Search spot-price grounding with native Pydantic schema enforcement.",
  },
  {
    id: "gemini-3.5-flash-lite",
    name: "Gemini 3.5 Flash-Lite",
    provider: "Google Gemini",
    priorityRank: 2,
    priorityTier: "Secondary Gemini (Slot #2)",
    strengthScore: 8.4,
    speedLabel: "Ultra-Fast",
    contextWindow: "1M tokens",
    defaultRpd: 250,
    defaultTpd: null,
    summary:
      "Separate Google AI Studio rate-limit bucket that absorbs overflow immediately if Gemini 3.8 Flash hits per-minute or daily caps.",
  },
  {
    id: "openai/gpt-oss-120b",
    name: "GPT-OSS 120B",
    provider: "Groq",
    priorityRank: 3,
    priorityTier: "Flagship Fallback (Slot #3)",
    strengthScore: 9.2,
    speedLabel: "~500 tok/s",
    contextWindow: "131K tokens",
    defaultRpd: 1000,
    defaultTpd: 200000,
    summary:
      "120B MoE reasoning model on Groq LPU hardware; top open-weight fallback for multi-hop supply-chain and margin pass-through extraction.",
  },
  {
    id: "qwen/qwen3.8-27b",
    name: "Qwen 3.8 27B",
    provider: "Groq",
    priorityRank: 4,
    priorityTier: "Multilingual Fallback (Slot #4)",
    strengthScore: 8.6,
    speedLabel: "~650 tok/s",
    contextWindow: "131K tokens",
    defaultRpd: 1000,
    defaultTpd: 200000,
    summary:
      "Strong structured JSON adherence and cross-lingual comprehension for Japanese, Korean, Taiwanese, and European materials disclosures.",
  },
  {
    id: "openai/gpt-oss-20b",
    name: "GPT-OSS 20B",
    provider: "Groq",
    priorityRank: 5,
    priorityTier: "High-Throughput Fallback (Slot #5)",
    strengthScore: 8.0,
    speedLabel: "~900 tok/s",
    contextWindow: "131K tokens",
    defaultRpd: 1000,
    defaultTpd: 200000,
    summary:
      "Lightweight 20B MoE model providing an independent 1,000 RPD / 200K TPD bucket to finish batch runs when larger buckets saturate.",
  },
  {
    id: "openai/gpt-oss-safeguard-20b",
    name: "GPT-OSS Safeguard 20B",
    provider: "Groq",
    priorityRank: 6,
    priorityTier: "Last-Resort Reserve (Slot #6)",
    strengthScore: 7.5,
    speedLabel: "~850 tok/s",
    contextWindow: "128K tokens",
    defaultRpd: 1000,
    defaultTpd: 200000,
    summary:
      "Safety-tuned 20B reserve bucket; placed last because stricter classifier guardrails can occasionally refuse defense/munitions filing excerpts.",
  },
];

interface UsageRow {
  model_id: string;
  calls_used: number;
  tokens_used: number;
  remaining_requests: number | null;
  limit_requests: number | null;
  remaining_tokens: number | null;
  limit_tokens: number | null;
  quota_exhausted: boolean;
  last_used_at: string | null;
}

async function AiModelsLoader() {
  await connection();
  const sql = neon(process.env.DATABASE_URL!);

  // Pacific Time date matches Google AI Studio daily reset window
  const ptDate = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Los_Angeles",
  }).format(new Date());

  const rows = (await sql`
    SELECT
      model_id,
      calls_used,
      tokens_used,
      remaining_requests,
      limit_requests,
      remaining_tokens,
      limit_tokens,
      quota_exhausted,
      last_used_at::text AS last_used_at
    FROM ai_model_usage
    WHERE usage_date = ${ptDate}::date
  `.catch(() => [])) as UsageRow[];

  const usageMap = new Map<string, UsageRow>();
  for (const r of rows) {
    usageMap.set(r.model_id, r);
  }

  return (
    <>
      <div className="rounded-xl bg-[#111827]/80 border border-slate-800 p-4 text-xs text-slate-300 leading-relaxed flex flex-col sm:flex-row sm:items-center justify-between gap-2">
        <span>
          Every pipeline job (<code className="font-mono text-sky-400">filings_ai</code>,{" "}
          <code className="font-mono text-sky-400">extract_products</code>,{" "}
          <code className="font-mono text-sky-400">backfill_enrichment</code>, and{" "}
          <code className="font-mono text-sky-400">ai_spot_prices</code>) routes requests in strict priority order below, failing over automatically on{" "}
          <code className="font-mono text-amber-300">429</code> rate limits,{" "}
          <code className="font-mono text-amber-300">503</code> outages, or 45-second timeouts.
        </span>
        <span className="text-[11px] font-mono text-slate-400 shrink-0">
          Window: {ptDate} (PT)
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {MODEL_REGISTRY.map((m) => {
          const u = usageMap.get(m.id);
          const limitReq = u?.limit_requests ?? m.defaultRpd;
          const callsUsed = u?.calls_used ?? 0;
          const remainingReq = u?.quota_exhausted
            ? 0
            : u?.remaining_requests != null
            ? u.remaining_requests
            : Math.max(0, limitReq - callsUsed);
          const pctLeft =
            limitReq > 0
              ? Math.max(0, Math.min(100, (remainingReq / limitReq) * 100))
              : 0;

          const limitTok = u?.limit_tokens ?? m.defaultTpd;
          const remTok = u?.quota_exhausted
            ? 0
            : u?.remaining_tokens != null
            ? u.remaining_tokens
            : limitTok != null
            ? Math.max(0, limitTok - (u?.tokens_used ?? 0))
            : null;

          return (
            <div
              key={m.id}
              className="rounded-xl border border-slate-800 bg-[#111827]/90 p-5 flex flex-col justify-between space-y-4"
            >
              <div className="space-y-2.5">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="px-2 py-0.5 rounded bg-sky-500/15 text-sky-300 border border-sky-500/30 text-[11px] font-mono font-bold">
                        #{m.priorityRank} Priority
                      </span>
                      <span className="text-[11px] font-mono text-slate-400">
                        {m.provider}
                      </span>
                    </div>
                    <h2 className="text-sm font-semibold text-white mt-1.5">
                      {m.name}
                    </h2>
                    <div className="text-[11px] font-mono text-slate-500">
                      {m.id} • {m.contextWindow} • {m.speedLabel}
                    </div>
                  </div>

                  <div className="text-right shrink-0">
                    <div className="text-[10px] font-mono uppercase text-slate-400">
                      Strength
                    </div>
                    <div className="text-sm font-mono font-bold text-emerald-400">
                      {m.strengthScore.toFixed(1)}
                      <span className="text-xs text-slate-500">/10</span>
                    </div>
                  </div>
                </div>

                {/* Strength Rating Bar */}
                <div className="w-full h-1.5 rounded-full bg-slate-800 overflow-hidden">
                  <div
                    className="h-full rounded-full bg-emerald-400"
                    style={{ width: `${m.strengthScore * 10}%` }}
                  />
                </div>

                <p className="text-xs text-slate-300 leading-relaxed">
                  {m.summary}
                </p>
              </div>

              {/* Live Daily Quota Remaining */}
              <div className="rounded-lg bg-[#090d16] border border-slate-800/90 p-3 space-y-2">
                <div className="flex items-center justify-between text-xs font-mono">
                  <span className="text-slate-400">Requests Left Today</span>
                  <span
                    className={
                      pctLeft > 40
                        ? "text-emerald-300 font-semibold"
                        : pctLeft > 10
                        ? "text-amber-300 font-semibold"
                        : "text-rose-400 font-semibold"
                    }
                  >
                    {remainingReq.toLocaleString()} / {limitReq.toLocaleString()} ({pctLeft.toFixed(0)}%)
                  </span>
                </div>

                <div className="w-full h-2 rounded-full bg-slate-800 overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${
                      pctLeft > 40
                        ? "bg-sky-400"
                        : pctLeft > 10
                        ? "bg-amber-400"
                        : "bg-rose-500"
                    }`}
                    style={{ width: `${pctLeft}%` }}
                  />
                </div>

                <div className="flex items-center justify-between text-[10px] font-mono text-slate-500">
                  <span>Used today: {callsUsed} calls</span>
                  {remTok != null && limitTok != null ? (
                    <span>
                      Tokens left: {Math.round(remTok / 1000)}K /{" "}
                      {Math.round(limitTok / 1000)}K
                    </span>
                  ) : (
                    <span>
                      {u?.last_used_at
                        ? `Last call: ${u.last_used_at.slice(11, 16)} UTC`
                        : "Idle today"}
                    </span>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}

export default function AiModelsPage() {
  return (
    <div className="min-h-screen bg-[#090d16] text-slate-100 font-sans">
      <header className="border-b border-slate-800/80 bg-[#0c1220]/90 backdrop-blur">
        <div className="max-w-5xl mx-auto px-6 py-4 flex items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <span className="h-2 w-2 rounded-full bg-sky-400" />
              <h1 className="text-base font-semibold text-white">
                AI Ingestion Cascade &amp; Daily Quota Telemetry
              </h1>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              6-model automatic failover chain (2 Gemini + 4 Groq)
            </p>
          </div>
          <Link
            href="/"
            className="px-3 py-1.5 rounded-lg bg-slate-800/80 hover:bg-slate-800 border border-slate-700/80 text-xs text-slate-200 transition"
          >
            &larr; Back to Terminal
          </Link>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-8 space-y-6">
        <Suspense
          fallback={
            <div className="rounded-xl bg-[#111827]/80 border border-slate-800 p-6 text-xs font-mono text-slate-400">
              Loading AI model quota telemetry...
            </div>
          }
        >
          <AiModelsLoader />
        </Suspense>
      </main>
    </div>
  );
}
