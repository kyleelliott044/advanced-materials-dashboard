"use client";

import { useState, useEffect, useMemo } from "react";

type Timeframe = "1M" | "6M" | "1Y" | "5Y";

interface ChartPoint {
  date: string;
  close: number;
}

const TIMEFRAMES: Timeframe[] = ["1M", "6M", "1Y", "5Y"];

export default function StockPriceChart({ ticker }: { ticker: string }) {
  const [timeframe, setTimeframe] = useState<Timeframe>("1Y");
  const [points, setPoints] = useState<ChartPoint[]>([]);
  const [currency, setCurrency] = useState("USD");
  const [loading, setLoading] = useState(true);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setHoverIdx(null);

    fetch(
      `/api/chart?ticker=${encodeURIComponent(ticker)}&range=${timeframe}`
    )
      .then((r) => r.json())
      .then((data) => {
        if (cancelled) return;
        setPoints(Array.isArray(data.points) ? data.points : []);
        if (data.currency) setCurrency(data.currency);
      })
      .catch(() => {
        if (!cancelled) setPoints([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [ticker, timeframe]);

  const stats = useMemo(() => {
    if (points.length < 2) return null;
    const first = points[0].close;
    const activePoint =
      hoverIdx != null && points[hoverIdx]
        ? points[hoverIdx]
        : points[points.length - 1];
    const diff = activePoint.close - first;
    const pct = first !== 0 ? (diff / first) * 100 : 0;
    let min = Infinity;
    let max = -Infinity;
    for (const p of points) {
      if (p.close < min) min = p.close;
      if (p.close > max) max = p.close;
    }
    return {
      current: activePoint.close,
      activeDate: activePoint.date,
      diff,
      pct,
      isUp: diff >= 0,
      min,
      max,
    };
  }, [points, hoverIdx]);

  // SVG coordinate projection (640 x 150 viewBox)
  const W = 640;
  const H = 150;
  const PAD_X = 6;
  const PAD_Y = 12;

  const { linePath, areaPath, coords } = useMemo(() => {
    if (points.length < 2 || !stats) {
      return { linePath: "", areaPath: "", coords: [] };
    }
    const span = stats.max - stats.min || 1;
    const mapped = points.map((pt, idx) => {
      const x =
        PAD_X + (idx / (points.length - 1)) * (W - PAD_X * 2);
      const y =
        H -
        PAD_Y -
        ((pt.close - stats.min) / span) * (H - PAD_Y * 2);
      return { x, y, ...pt };
    });

    const line = mapped
      .map((c, i) => `${i === 0 ? "M" : "L"}${c.x.toFixed(1)},${c.y.toFixed(1)}`)
      .join(" ");
    const area = `${line} L${mapped[mapped.length - 1].x.toFixed(1)},${H} L${mapped[0].x.toFixed(1)},${H} Z`;

    return { linePath: line, areaPath: area, coords: mapped };
  }, [points, stats]);

  const strokeColor = stats?.isUp ? "#34d399" : "#fb7185";
  const gradId = `priceGrad-${ticker.replace(/[^a-zA-Z0-9]/g, "")}`;

  return (
    <div className="rounded-xl bg-[#090d16] border border-slate-800/90 p-3.5 space-y-2.5">
      {/* Top Row: Price, Period Return & Timeframe Switcher */}
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-baseline gap-2.5 flex-wrap">
          {stats ? (
            <>
              <span className="text-base font-mono font-bold text-white">
                {stats.current.toLocaleString(undefined, {
                  minimumFractionDigits: 2,
                  maximumFractionDigits: 2,
                })}{" "}
                <span className="text-[10px] font-normal text-slate-400">
                  {currency}
                </span>
              </span>
              <span
                className={`px-1.5 py-0.5 rounded text-[11px] font-mono font-medium ${
                  stats.isUp
                    ? "bg-emerald-500/15 text-emerald-300"
                    : "bg-rose-500/15 text-rose-300"
                }`}
              >
                {stats.isUp ? "+" : ""}
                {stats.pct.toFixed(2)}% ({timeframe})
              </span>
              <span className="text-[10px] font-mono text-slate-400">
                {stats.activeDate}
              </span>
            </>
          ) : (
            <span className="text-xs font-mono text-slate-400">
              {loading ? "Loading price history..." : "Price chart unavailable"}
            </span>
          )}
        </div>

        <div className="flex items-center gap-1 bg-[#111827] border border-slate-800 rounded-lg p-0.5">
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf}
              type="button"
              onClick={() => setTimeframe(tf)}
              className={`px-2 py-0.5 rounded-md text-[11px] font-mono font-medium transition ${
                timeframe === tf
                  ? "bg-sky-500 text-white"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              {tf}
            </button>
          ))}
        </div>
      </div>

      {/* Interactive SVG Area Chart */}
      <div className="relative h-36 w-full">
        {loading ? (
          <div className="h-full w-full rounded-lg bg-slate-900/50 animate-pulse" />
        ) : coords.length < 2 ? (
          <div className="h-full w-full flex items-center justify-center text-xs text-slate-500">
            No historical quotes returned for {ticker} ({timeframe}).
          </div>
        ) : (
          <svg
            viewBox={`0 0 ${W} ${H}`}
            preserveAspectRatio="none"
            className="w-full h-full overflow-visible cursor-crosshair"
            onMouseLeave={() => setHoverIdx(null)}
            onMouseMove={(e) => {
              const rect = e.currentTarget.getBoundingClientRect();
              const relX = Math.max(
                0,
                Math.min(1, (e.clientX - rect.left) / rect.width)
              );
              const idx = Math.round(relX * (coords.length - 1));
              setHoverIdx(idx);
            }}
          >
            <defs>
              <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={strokeColor} stopOpacity="0.28" />
                <stop offset="100%" stopColor={strokeColor} stopOpacity="0.0" />
              </linearGradient>
            </defs>

            <path d={areaPath} fill={`url(#${gradId})`} />
            <path
              d={linePath}
              fill="none"
              stroke={strokeColor}
              strokeWidth="2"
              vectorEffect="non-scaling-stroke"
            />

            {hoverIdx != null && coords[hoverIdx] && (
              <>
                <line
                  x1={coords[hoverIdx].x}
                  x2={coords[hoverIdx].x}
                  y1={0}
                  y2={H}
                  stroke="#64748b"
                  strokeWidth="1"
                  strokeDasharray="3 3"
                  vectorEffect="non-scaling-stroke"
                />
                <circle
                  cx={coords[hoverIdx].x}
                  cy={coords[hoverIdx].y}
                  r="4"
                  fill={strokeColor}
                  stroke="#090d16"
                  strokeWidth="1.5"
                />
              </>
            )}
          </svg>
        )}
      </div>

      {/* Bottom High / Low Strip */}
      {stats && (
        <div className="flex items-center justify-between text-[10px] font-mono text-slate-500 pt-0.5 border-t border-slate-800/70">
          <span>
            {timeframe} Low:{" "}
            <strong className="text-slate-300">{stats.min.toFixed(2)}</strong>
          </span>
          <span>
            {points[0]?.date} &rarr; {points[points.length - 1]?.date}
          </span>
          <span>
            {timeframe} High:{" "}
            <strong className="text-slate-300">{stats.max.toFixed(2)}</strong>
          </span>
        </div>
      )}
    </div>
  );
}
