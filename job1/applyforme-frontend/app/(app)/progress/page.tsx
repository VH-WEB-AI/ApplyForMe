"use client";

import { useEffect, useState } from "react";
import { getApplications } from "@/lib/api";
import type { ApplicationStage } from "@/lib/types";

const STAGES: ApplicationStage["stage"][] = ["applied", "screening", "interview", "offer", "rejected"];
const STAGE_LABEL: Record<ApplicationStage["stage"], string> = {
  applied: "Applied",
  screening: "Screening",
  interview: "Interview",
  offer: "Offer",
  rejected: "Rejected",
};

export default function ProgressPage() {
  const [apps, setApps] = useState<ApplicationStage[]>([]);

  useEffect(() => {
    getApplications().then(setApps);
  }, []);

  return (
    <div>
      <header className="mb-8">
        <span className="font-mono text-xs tracking-[0.3em] text-mint uppercase">Applications</span>
        <h1 className="font-display text-3xl font-semibold mt-1">Track Progress</h1>
        <p className="text-muted mt-1">Every application, staged along the same pipeline.</p>
      </header>

      <div className="flex flex-col gap-4">
        {apps.map((app) => {
          const stageIndex = STAGES.indexOf(app.stage);
          const isRejected = app.stage === "rejected";
          return (
            <article key={app.id} className="bg-panel panel-border rounded-xl p-6">
              <div className="flex flex-wrap items-baseline justify-between gap-2 mb-4">
                <div>
                  <h2 className="font-display font-semibold">{app.jobTitle}</h2>
                  <p className="text-sm text-muted">{app.company}</p>
                </div>
                <span className="text-xs text-muted font-mono">
                  updated {new Date(app.updatedAt).toLocaleDateString()}
                </span>
              </div>

              <ol className="flex items-center">
                {STAGES.filter((s) => s !== "rejected").map((stage, i) => {
                  const reached = !isRejected && stageIndex >= i;
                  return (
                    <li key={stage} className="flex items-center flex-1 last:flex-none">
                      <div className="flex flex-col items-center gap-1.5">
                        <div
                          className={`w-7 h-7 rounded-full flex items-center justify-center font-mono text-xs ${
                            reached ? "bg-signal text-ink" : "bg-panel-raised text-muted"
                          }`}
                        >
                          {i + 1}
                        </div>
                        <span className={`text-[11px] font-mono uppercase tracking-wider ${reached ? "text-ivory" : "text-muted"}`}>
                          {STAGE_LABEL[stage]}
                        </span>
                      </div>
                      {i < STAGES.length - 2 && (
                        <div className={`flex-1 h-px mx-2 ${reached && stageIndex > i ? "bg-signal" : "bg-rail"}`} />
                      )}
                    </li>
                  );
                })}
              </ol>
              {isRejected && (
                <p className="text-danger text-xs font-mono uppercase tracking-wider mt-3">Not moving forward</p>
              )}
            </article>
          );
        })}
      </div>
    </div>
  );
}
