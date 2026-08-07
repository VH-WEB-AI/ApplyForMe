"use client";

import { useEffect, useState } from "react";
import Gauge from "@/components/Gauge";
import { getCareerHealth } from "@/lib/api";
import type { CareerHealth } from "@/lib/types";

function Sparkline({ points }: { points: { label: string; score: number }[] }) {
  if (points.length < 2) {
    return <p className="text-sm text-muted">Trend history will appear as more activity is recorded.</p>;
  }
  const w = 400;
  const h = 100;
  const max = Math.max(...points.map((p) => p.score), 100);
  const min = Math.min(...points.map((p) => p.score), 0);
  const step = w / (points.length - 1);
  const path = points
    .map((p, i) => {
      const x = i * step;
      const y = h - ((p.score - min) / (max - min || 1)) * h;
      return `${i === 0 ? "M" : "L"}${x},${y}`;
    })
    .join(" ");

  return (
    <svg viewBox={`0 0 ${w} ${h + 24}`} className="w-full">
      <path d={path} fill="none" stroke="#5EEAD4" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round" />
      {points.map((p, i) => {
        const x = i * step;
        const y = h - ((p.score - min) / (max - min || 1)) * h;
        return (
          <g key={p.label}>
            <circle cx={x} cy={y} r={4} fill="#0A1412" stroke="#5EEAD4" strokeWidth={2} />
            <text x={x} y={h + 18} textAnchor="middle" fontSize="11" fill="#8FA39C" className="font-mono">
              {p.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="max-h-72 overflow-auto rounded-lg bg-panel-raised p-4 text-xs leading-relaxed text-muted">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export default function CareerHealthPage() {
  const [health, setHealth] = useState<CareerHealth | null>(null);

  useEffect(() => {
    getCareerHealth().then(setHealth);
  }, []);

  return (
    <div>
      <header className="mb-8">
        <span className="font-mono text-xs tracking-[0.3em] text-mint uppercase">Engine 3</span>
        <h1 className="font-display text-3xl font-semibold mt-1">Career Health</h1>
        <p className="text-muted mt-1">An aggregate score across resume, matches, and activity, with weak areas surfaced.</p>
      </header>

      {!health ? (
        <p className="text-muted font-mono text-sm">Aggregating signals…</p>
      ) : (
        <div className="grid md:grid-cols-[auto,1fr] gap-8 items-start">
          <div className="bg-panel panel-border rounded-xl p-6 flex flex-col items-center">
            <Gauge
              score={health.score}
              label="Career Health"
              sublabel={`${health.trend === "up" ? "↑" : health.trend === "down" ? "↓" : "→"} ${health.trendDeltaPct}% vs last month`}
            />
          </div>

          <div className="flex flex-col gap-6">
            <div className="bg-panel panel-border rounded-xl p-6">
              <h2 className="font-display font-semibold text-lg mb-4">4-month trend</h2>
              <Sparkline points={health.history} />
            </div>

            <div className="grid sm:grid-cols-3 gap-4">
              {Object.entries(health.benchmarks).map(([key, value]) => (
                <div key={key} className="bg-panel panel-border rounded-lg p-4">
                  <p className="font-mono text-[10px] uppercase tracking-widest text-muted">
                    {key.replaceAll("_", " ")}
                  </p>
                  <p className="font-display text-xl mt-2">
                    {typeof value === "number" ? Math.round(value * (value <= 1 ? 100 : 1)) : String(value ?? "n/a")}
                  </p>
                </div>
              ))}
            </div>

            <div className="grid sm:grid-cols-2 gap-6">
              <div>
                <h2 className="font-display font-semibold text-lg mb-3">Weak areas</h2>
                <ul className="flex flex-col gap-2">
                  {health.weakAreas.map((w) => (
                    <li key={w} className="text-sm bg-panel panel-border rounded-lg p-3 text-muted">
                      {w}
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <h2 className="font-display font-semibold text-lg mb-3">Recommendations</h2>
                <ul className="flex flex-col gap-2">
                  {health.recommendations.map((r) => (
                    <li key={r} className="text-sm bg-panel panel-border rounded-lg p-3 leading-relaxed">
                      {r}
                    </li>
                  ))}
                </ul>
              </div>
            </div>

            <div className="grid md:grid-cols-2 gap-6">
              <div className="bg-panel panel-border rounded-xl p-5">
                <h2 className="font-display font-semibold text-lg mb-3">Reports & insights</h2>
                <ul className="flex flex-col gap-2 text-sm text-muted">
                  <li>Trend: {health.trend}</li>
                  <li>Weak areas identified: {health.weakAreas.length}</li>
                  <li>Personalized recommendations: {health.recommendations.length}</li>
                </ul>
              </div>
              <div className="bg-panel panel-border rounded-xl p-5">
                <h2 className="font-display font-semibold text-lg mb-3">JSON output</h2>
                <JsonBlock
                  value={{
                    career_health_score: health.score,
                    trend: health.trend,
                    weak_areas: health.weakAreas,
                    benchmarks: health.benchmarks,
                    recommendations: health.recommendations,
                  }}
                />
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
