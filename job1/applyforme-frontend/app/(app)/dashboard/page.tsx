"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import Gauge from "@/components/Gauge";
import { getCareerHealth, listResumes, matchJobs } from "@/lib/api";
import type { CareerHealth, JobMatch, Resume } from "@/lib/types";

export default function DashboardPage() {
  const [resume, setResume] = useState<Resume | null>(null);
  const [health, setHealth] = useState<CareerHealth | null>(null);
  const [matches, setMatches] = useState<JobMatch[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const resumes = await listResumes();
        const r = resumes[0] ?? null;
        setResume(r);
        const [h, m] = await Promise.all([getCareerHealth(), r ? matchJobs(r.id) : Promise.resolve([])]);
        setHealth(h);
        setMatches(m);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const avgMatch = matches.length
    ? Math.round(matches.reduce((sum, m) => sum + m.matchScore, 0) / matches.length)
    : 0;

  return (
    <div>
      <header className="mb-8">
        <span className="font-mono text-xs tracking-[0.3em] text-mint uppercase">Overview</span>
        <h1 className="font-display text-3xl font-semibold mt-1">Your instrument panel</h1>
        <p className="text-muted mt-1">Three signals, read at a glance, sourced live from each engine.</p>
      </header>

      <section className="bg-panel panel-border rounded-xl p-8 flex flex-wrap justify-around gap-8">
        {loading ? (
          <p className="text-muted font-mono text-sm">Reading instruments…</p>
        ) : (
          <>
            <Gauge score={resume?.atsScore ?? 0} label="ATS Score" sublabel="Resume Intelligence" />
            <Gauge score={health?.score ?? 0} label="Career Health" sublabel={`${health?.trend === "up" ? "↑" : health?.trend === "down" ? "↓" : "→"} ${health?.trendDeltaPct ?? 0}% this month`} />
            <Gauge score={avgMatch} label="Avg. Match Rate" sublabel={`${matches.length} open matches`} />
          </>
        )}
      </section>

      <div className="grid md:grid-cols-2 gap-6 mt-6">
        <div className="bg-panel panel-border rounded-xl p-6">
          <h2 className="font-display font-semibold text-lg mb-3">Top suggestion</h2>
          {resume?.suggestions[0] ? (
            <p className="text-sm text-muted leading-relaxed">{resume.suggestions[0].message}</p>
          ) : (
            <p className="text-sm text-muted">No suggestions yet — upload a resume to get started.</p>
          )}
          <Link href="/resume" className="inline-block mt-4 text-sm text-mint hover:underline">
            Review resume →
          </Link>
        </div>
        <div className="bg-panel panel-border rounded-xl p-6">
          <h2 className="font-display font-semibold text-lg mb-3">Best match right now</h2>
          {matches[0] ? (
            <>
              <p className="font-semibold">{matches[0].title}</p>
              <p className="text-sm text-muted">{matches[0].company} · {matches[0].location}</p>
              <p className="text-sm text-muted mt-2 leading-relaxed">{matches[0].explanation}</p>
            </>
          ) : (
            <p className="text-sm text-muted">No matches yet.</p>
          )}
          <Link href="/jobs" className="inline-block mt-4 text-sm text-mint hover:underline">
            See all matches →
          </Link>
        </div>
      </div>
    </div>
  );
}
