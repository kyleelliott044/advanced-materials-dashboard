import { NextRequest, NextResponse } from "next/server";

const RANGE_CONFIG: Record<string, { range: string; interval: string }> = {
  "1M": { range: "1mo", interval: "1d" },
  "6M": { range: "6mo", interval: "1d" },
  "1Y": { range: "1y", interval: "1d" },
  "5Y": { range: "5y", interval: "1wk" },
};

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const ticker = (searchParams.get("ticker") || "").trim().toUpperCase();
  const timeframe = (searchParams.get("range") || "1Y").toUpperCase();

  if (!ticker || !/^[A-Z0-9.\-^=]{1,18}$/.test(ticker)) {
    return NextResponse.json({ error: "Invalid ticker" }, { status: 400 });
  }

  const cfg = RANGE_CONFIG[timeframe] || RANGE_CONFIG["1Y"];
  const yahooUrl = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(
    ticker
  )}?range=${cfg.range}&interval=${cfg.interval}&includePrePost=false`;

  try {
    const res = await fetch(yahooUrl, {
      headers: {
        "User-Agent": "Mozilla/5.0 (compatible; MaterialsMacroTerminal/1.0)",
      },
      next: { revalidate: 900 },
    });

    if (!res.ok) {
      return NextResponse.json(
        { error: `Upstream HTTP ${res.status}` },
        { status: 502 }
      );
    }

    const data = await res.json();
    const result = data?.chart?.result?.[0];
    if (!result) {
      return NextResponse.json({ points: [], currency: "USD" });
    }

    const timestamps: number[] = result.timestamp || [];
    const closes: (number | null)[] =
      result.indicators?.quote?.[0]?.close || [];
    const currency: string = result.meta?.currency || "USD";

    const points: { date: string; close: number }[] = [];
    for (let i = 0; i < timestamps.length; i++) {
      const c = closes[i];
      if (c != null && Number.isFinite(c)) {
        const d = new Date(timestamps[i] * 1000).toISOString().slice(0, 10);
        points.push({ date: d, close: Number(c.toFixed(2)) });
      }
    }

    return NextResponse.json({
      ticker,
      timeframe,
      currency,
      points,
    });
  } catch (err: any) {
    return NextResponse.json(
      { error: err?.message || "Chart fetch failed" },
      { status: 500 }
    );
  }
}
