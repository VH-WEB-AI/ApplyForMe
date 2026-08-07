"use client";

import { useEffect, useMemo, useState } from "react";
import { listResumes, matchJobs } from "@/lib/api";
import type { JobMatch, Resume } from "@/lib/types";

const SAMPLE_JOB =
  "We are hiring an AI/ML Developer with strong Python, machine learning, FastAPI, SQL, Docker, and cloud deployment experience. The role includes building LLM-powered features, RAG workflows, model evaluation pipelines, and production APIs.";

function SkillChips({ skills, tone }: { skills: string[]; tone: "match" | "missing" }) {
  if (!skills.length) return <p className="text-xs text-muted">None returned</p>;
  return (
    <div className="flex flex-wrap gap-2">
      {skills.map((skill) => (
        <span
          key={skill}
          className={`rounded-full bg-panel-raised px-3 py-1 text-[10px] font-mono uppercase tracking-wider ${
            tone === "match" ? "text-mint" : "text-signal"
          }`}
        >
          {skill}
        </span>
      ))}
    </div>
  );
}

function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="max-h-72 overflow-auto rounded-lg bg-panel-raised p-4 text-xs leading-relaxed text-muted">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export default function JobsPage() {
  const [resumes, setResumes] = useState<Resume[]>([]);
  const [resumeId, setResumeId] = useState("");
  const [jobTitle, setJobTitle] = useState("AI/ML Developer");
  const [company, setCompany] = useState("Target company");
  const [location, setLocation] = useState("Remote");
  const [jobDescription, setJobDescription] = useState(SAMPLE_JOB);
  const [matches, setMatches] = useState<JobMatch[]>([]);
  const [loadingResumes, setLoadingResumes] = useState(true);
  const [matching, setMatching] = useState(false);
  const [error, setError] = useState("");

  const selectedResume = useMemo(
    () => resumes.find((resume) => resume.id === resumeId),
    [resumes, resumeId]
  );

  useEffect(() => {
    listResumes()
      .then((items) => {
        setResumes(items);
        setResumeId(items[0]?.id ?? "");
      })
      .catch((err) => setError((err as Error).message))
      .finally(() => setLoadingResumes(false));
  }, []);

  async function handleMatch() {
    setError("");
    setMatches([]);
    if (!resumeId) {
      setError("Upload and score a resume before running job match.");
      return;
    }
    if (!jobDescription.trim()) {
      setError("Paste a job description before running the match.");
      return;
    }

    setMatching(true);
    try {
      const result = await matchJobs(resumeId, jobDescription, {
        title: jobTitle || "Job match",
        company: company || "Company not specified",
        location: location || "Location not specified",
        visa_sponsorship: true,
      });
      setMatches(result);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setMatching(false);
    }
  }

  return (
    <div>
      <header className="mb-8">
        <span className="font-mono text-xs tracking-[0.3em] text-mint uppercase">Engine 2</span>
        <h1 className="font-display text-3xl font-semibold mt-1">Job Match</h1>
        <p className="text-muted mt-1">Embedding similarity plus hard constraints (visa, salary), explained in plain language.</p>
      </header>

      <section className="grid lg:grid-cols-[360px,1fr] gap-6 items-start">
        <div className="bg-panel panel-border rounded-xl p-5">
          <h2 className="font-display font-semibold text-lg">Match inputs</h2>

          <label className="block mt-5">
            <span className="font-mono text-[10px] uppercase tracking-widest text-muted">Resume</span>
            <select
              value={resumeId}
              onChange={(event) => setResumeId(event.target.value)}
              disabled={loadingResumes || !resumes.length}
              className="mt-2 w-full rounded-lg panel-border bg-panel-raised px-3 py-2 text-sm"
            >
              {loadingResumes && <option>Loading resumes...</option>}
              {!loadingResumes && !resumes.length && <option>No resumes found</option>}
              {resumes.map((resume) => (
                <option key={resume.id} value={resume.id}>
                  {resume.filename}
                </option>
              ))}
            </select>
          </label>

          <div className="grid gap-3 mt-4">
            <label>
              <span className="font-mono text-[10px] uppercase tracking-widest text-muted">Title</span>
              <input
                value={jobTitle}
                onChange={(event) => setJobTitle(event.target.value)}
                className="mt-2 w-full rounded-lg panel-border bg-panel-raised px-3 py-2 text-sm"
              />
            </label>
            <label>
              <span className="font-mono text-[10px] uppercase tracking-widest text-muted">Company</span>
              <input
                value={company}
                onChange={(event) => setCompany(event.target.value)}
                className="mt-2 w-full rounded-lg panel-border bg-panel-raised px-3 py-2 text-sm"
              />
            </label>
            <label>
              <span className="font-mono text-[10px] uppercase tracking-widest text-muted">Location</span>
              <input
                value={location}
                onChange={(event) => setLocation(event.target.value)}
                className="mt-2 w-full rounded-lg panel-border bg-panel-raised px-3 py-2 text-sm"
              />
            </label>
          </div>

          {selectedResume && (
            <p className="text-xs text-muted mt-4">
              Current resume status: <span className="text-mint">{selectedResume.status}</span>
            </p>
          )}

          <button
            type="button"
            onClick={handleMatch}
            disabled={matching || loadingResumes}
            className="mt-5 w-full rounded-lg bg-signal px-4 py-2 font-display font-semibold text-ink disabled:cursor-not-allowed disabled:opacity-50"
          >
            {matching ? "Computing match..." : "Run match"}
          </button>
        </div>

        <div className="flex flex-col gap-5">
          <label className="block">
            <span className="font-mono text-[10px] uppercase tracking-widest text-muted">Job description</span>
            <textarea
              value={jobDescription}
              onChange={(event) => setJobDescription(event.target.value)}
              rows={10}
              className="mt-2 w-full rounded-xl panel-border bg-panel px-4 py-3 text-sm leading-relaxed text-white"
            />
          </label>

          {error && (
            <div className="rounded-lg border border-danger/60 bg-danger/10 p-4 text-sm text-danger">
              {error}
            </div>
          )}

          {!matches.length && !error && (
            <div className="rounded-xl panel-border bg-panel p-6 text-sm text-muted">
              Paste a target job description and run the match against your latest resume.
            </div>
          )}

          {matches.map((job) => (
            <article key={job.id} className="bg-panel panel-border rounded-xl p-6 flex gap-6 items-start">
              <div className="w-16 shrink-0 flex flex-col items-center">
                <span
                  className="font-mono text-2xl font-semibold"
                  style={{ color: job.matchScore >= 80 ? "#5EEAD4" : job.matchScore >= 60 ? "#FF8A3D" : "#FF6B5C" }}
                >
                  {job.matchScore}
                </span>
                <span className="text-[10px] text-muted uppercase tracking-wider">match</span>
              </div>
              <div className="flex-1">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h2 className="font-display font-semibold text-lg">{job.title}</h2>
                  <div className="flex gap-2">
                    {job.visaFriendly && (
                      <span className="text-[10px] font-mono uppercase tracking-wider bg-panel-raised px-2 py-1 rounded-full text-mint">
                        Visa OK
                      </span>
                    )}
                    <span
                      className={`text-[10px] font-mono uppercase tracking-wider px-2 py-1 rounded-full ${
                        job.hardConstraintsSatisfied ? "bg-panel-raised text-mint" : "bg-panel-raised text-danger"
                      }`}
                    >
                      {job.hardConstraintsSatisfied ? "Constraints OK" : "Constraint gap"}
                    </span>
                  </div>
                </div>
                <p className="text-sm text-muted mt-0.5">
                  {job.company} · {job.location}
                </p>
                <p className="font-mono text-[10px] uppercase tracking-widest text-signal mt-3">
                  {job.recommendation.replaceAll("_", " ")}
                </p>
                <p className="text-sm mt-3 leading-relaxed">{job.explanation}</p>

                <div className="grid md:grid-cols-2 gap-4 mt-5">
                  <div>
                    <h3 className="font-display text-sm font-semibold mb-2">Matched skills</h3>
                    <SkillChips skills={job.matchedSkills} tone="match" />
                  </div>
                  <div>
                    <h3 className="font-display text-sm font-semibold mb-2">Missing skills</h3>
                    <SkillChips skills={job.missingSkills} tone="missing" />
                  </div>
                </div>

                <div className="mt-5">
                  <h3 className="font-display text-sm font-semibold mb-2">JSON output</h3>
                  <JsonBlock
                    value={{
                      match_score: job.matchScore / 100,
                      matched_skills: job.matchedSkills,
                      missing_skills: job.missingSkills,
                      recommendation: job.recommendation,
                      hard_constraints_satisfied: job.hardConstraintsSatisfied,
                      explanation: job.explanation,
                    }}
                  />
                </div>
              </div>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
