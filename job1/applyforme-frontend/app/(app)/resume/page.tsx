"use client";

import { useEffect, useRef, useState } from "react";
import Gauge from "@/components/Gauge";
import { listResumes, uploadResume } from "@/lib/api";
import type { Resume } from "@/lib/types";

const CATEGORY_LABEL: Record<string, string> = {
  structure: "Structure",
  keywords: "Keywords",
  impact: "Impact",
  clarity: "Clarity",
};

function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="max-h-72 overflow-auto rounded-lg bg-panel-raised p-4 text-left text-xs leading-relaxed text-muted">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export default function ResumePage() {
  const [resume, setResume] = useState<Resume | null>(null);
  const [status, setStatus] = useState<"idle" | "uploading" | "processing" | "done">("idle");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    listResumes().then((resumes) => setResume(resumes[0] ?? null)).catch(() => setResume(null));
  }, []);

  async function handleFile(file: File) {
    setStatus("uploading");
    setTimeout(() => setStatus("processing"), 400);
    const result = await uploadResume(file);
    setResume(result);
    setStatus("done");
  }

  return (
    <div>
      <header className="mb-8">
        <span className="font-mono text-xs tracking-[0.3em] text-mint uppercase">Engine 1</span>
        <h1 className="font-display text-3xl font-semibold mt-1">Resume Intelligence</h1>
        <p className="text-muted mt-1">Parsing, ATS scoring, skill extraction, and improvement suggestions.</p>
      </header>

      <section
        className="panel-border border-dashed rounded-xl p-8 text-center cursor-pointer hover:border-signal transition"
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.doc,.docx"
          className="hidden"
          onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])}
        />
        <p className="font-display font-semibold">Drop your resume here, or click to browse</p>
        <p className="text-sm text-muted mt-1">PDF or DOCX — parsed and scored in the background</p>
        {status !== "idle" && (
          <p className="font-mono text-xs text-signal mt-4 uppercase tracking-wider">
            {status === "uploading" && "Uploading…"}
            {status === "processing" && "Queued — parsing & scoring…"}
            {status === "done" && "Scored"}
          </p>
        )}
      </section>

      {resume && (
        <div className="grid lg:grid-cols-[320px,1fr] gap-8 mt-8 items-start">
          <div className="bg-panel panel-border rounded-xl p-6 flex flex-col gap-6 items-center">
            <Gauge score={resume.atsScore ?? 0} label="ATS Score" sublabel={resume.filename} />
            <Gauge score={resume.resumeScore ?? resume.atsScore ?? 0} label="Resume Score" sublabel={resume.status} />
          </div>

          <div className="flex flex-col gap-6">
            <div>
              <h2 className="font-display font-semibold text-lg mb-3">Extracted skills</h2>
              <div className="flex flex-wrap gap-2">
                {resume.skills.map((skill) => (
                  <span key={skill} className="bg-panel-raised panel-border rounded-full px-3 py-1 text-xs font-mono">
                    {skill}
                  </span>
                ))}
              </div>
            </div>

            <div>
              <h2 className="font-display font-semibold text-lg mb-3">Suggestions & improvements</h2>
              <ul className="flex flex-col gap-3">
                {resume.suggestions.map((s) => (
                  <li key={s.id} className="bg-panel panel-border rounded-lg p-4">
                    <span className="font-mono text-[10px] tracking-widest text-signal uppercase">
                      {CATEGORY_LABEL[s.category] ?? "Suggestion"}
                    </span>
                    <p className="text-sm mt-1 leading-relaxed">{s.message}</p>
                  </li>
                ))}
              </ul>
            </div>

            <div className="grid md:grid-cols-2 gap-6">
              <div className="bg-panel panel-border rounded-xl p-5">
                <h2 className="font-display font-semibold text-lg mb-3">Parsed structure</h2>
                <div className="grid grid-cols-2 gap-3 text-sm">
                  <div className="bg-panel-raised rounded-lg p-3">
                    <p className="font-mono text-[10px] uppercase tracking-widest text-muted">Work history</p>
                    <p className="font-display text-2xl mt-1">{resume.structuredData.work_history?.length ?? 0}</p>
                  </div>
                  <div className="bg-panel-raised rounded-lg p-3">
                    <p className="font-mono text-[10px] uppercase tracking-widest text-muted">Education</p>
                    <p className="font-display text-2xl mt-1">{resume.structuredData.education?.length ?? 0}</p>
                  </div>
                </div>
              </div>
              <div className="bg-panel panel-border rounded-xl p-5">
                <h2 className="font-display font-semibold text-lg mb-3">Structured JSON response</h2>
                <JsonBlock
                  value={{
                    ats_score: resume.atsScore,
                    resume_score: resume.resumeScore,
                    extracted_skills: resume.skills,
                    structured_data: resume.structuredData,
                    suggestions: resume.suggestions.map((s) => s.message),
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
