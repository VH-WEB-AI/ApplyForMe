// ---------------------------------------------------------------------------
// Single API seam.
//
// The README is explicit: "the frontend should never call the LLM directly."
// Every one of these functions is the ONLY place that talks to the backend.
// Right now they resolve mock data (see mock-data.ts) so the UI can be built
// and demoed without the real API running. To connect the real backend:
//
//   1. Set NEXT_PUBLIC_API_BASE_URL in .env.local (e.g. http://localhost:8000)
//   2. Set USE_MOCK = false below
//   3. Each function's real fetch() call is already written — it mirrors the
//      endpoints documented in the backend README exactly (path, method,
//      body shape). No other file in the app needs to change.
// ---------------------------------------------------------------------------

import type {
  ApplicationStage,
  CareerHealth,
  CopilotMessage,
  JobMatch,
  Resume,
  User,
} from "./types";
import {
  mockApplications,
  mockCareerHealth,
  mockCopilotThread,
  mockJobMatches,
  mockResume,
  mockUser,
} from "./mock-data";

const USE_MOCK = false;
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

function authHeaders(): HeadersInit {
  const token = typeof window !== "undefined" ? localStorage.getItem("afm_token") : null;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function clearStoredAuth() {
  if (typeof window === "undefined") return;
  localStorage.removeItem("afm_token");
  localStorage.removeItem("afm_refresh_token");
}

function redirectToLogin() {
  if (typeof window === "undefined") return;
  const next = encodeURIComponent(`${window.location.pathname}${window.location.search}`);
  window.location.assign(`/login?next=${next}`);
}

async function delay<T>(value: T, ms = 500): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

// ---- Shared error handling ------------------------------------------------
//
// Surfaces the ACTUAL backend error message instead of a generic one.
// FastAPI/Pydantic validation errors usually come back as:
//   { "detail": "some message" }  or
//   { "detail": [{ "msg": "...", "loc": [...] }, ...] }
// This handles both shapes plus plain-text/non-JSON error bodies.

async function parseErrorMessage(res: Response): Promise<string> {
  const contentType = res.headers.get("content-type") || "";
  try {
    if (contentType.includes("application/json")) {
      const body = await res.json();
      if (typeof body?.detail === "string") return body.detail;
      if (Array.isArray(body?.detail)) {
        return body.detail
          .map((d: any) => d?.msg ?? JSON.stringify(d))
          .join("; ");
      }
      if (typeof body?.error?.message === "string") return body.error.message;
      if (typeof body?.message === "string") return body.message;
      return JSON.stringify(body);
    }
    const text = await res.text();
    return text || res.statusText;
  } catch {
    return res.statusText || `HTTP ${res.status}`;
  }
}

class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function assertOk(res: Response, fallbackLabel: string): Promise<void> {
  if (!res.ok) {
    const message = await parseErrorMessage(res);
    if (res.status === 401) {
      clearStoredAuth();
      redirectToLogin();
    }
    throw new ApiError(`${fallbackLabel}: ${message}`, res.status);
  }
}

// ---- Auth --------------------------------------------------------------

export async function login(email: string, _password: string): Promise<{ user: User; token: string }> {
  if (USE_MOCK) return delay({ user: { ...mockUser, email }, token: "mock_token" });

  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password: _password }),
    });
  } catch (err) {
    // Network-level failure: backend down, wrong port, CORS block, etc.
    throw new Error(
      `Could not reach the API at ${API_BASE}. Is the backend running and is NEXT_PUBLIC_API_BASE_URL set correctly? (${(err as Error).message})`
    );
  }

  await assertOk(res, "Login failed");

  const data = await res.json();
  const token = data.access_token ?? data.token ?? data.accessToken;
  if (!token) {
    throw new Error("Login succeeded but no access token was found in the response.");
  }
  const refreshToken = data.refresh_token ?? data.refreshToken;
  if (refreshToken && typeof window !== "undefined") {
    localStorage.setItem("afm_refresh_token", refreshToken);
  }

  return {
    user: { ...mockUser, email },
    token,
  };
}

export async function register(
  email: string,
  _password: string,
  name: string
): Promise<{ user: User; token: string }> {
  if (USE_MOCK) return delay({ user: { ...mockUser, email, name }, token: "mock_token" });

  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/v1/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password: _password, full_name: name }),
    });
  } catch (err) {
    throw new Error(
      `Could not reach the API at ${API_BASE}. Is the backend running and is NEXT_PUBLIC_API_BASE_URL set correctly? (${(err as Error).message})`
    );
  }

  await assertOk(res, "Registration failed");

  const data = await res.json();
  const token = data.access_token ?? data.token ?? data.accessToken;
  if (!token) {
    throw new Error("Registration succeeded but no access token was found in the response.");
  }
  const refreshToken = data.refresh_token ?? data.refreshToken;
  if (refreshToken && typeof window !== "undefined") {
    localStorage.setItem("afm_refresh_token", refreshToken);
  }

  return {
    user: { ...mockUser, email, name },
    token,
  };
}

// ---- Engine 1: Resume Intelligence -------------------------------------
//
// Backend's ResumeOut (see app/schemas) uses different field names/shapes
// than the frontend's Resume type:
//   file_name        -> filename
//   ats_score        -> atsScore
//   resume_score     -> resumeScore
//   extracted_skills -> skills
//   suggestions: string[]  -> suggestions: ResumeSuggestion[]
//   structured_data  -> structuredData
//   created_at       -> uploadedAt
// This mapper bridges that gap so the rest of the app can rely on the
// frontend Resume shape without caring about backend field names.

function mapResumeOut(raw: any): Resume {
  return {
    id: raw.id,
    filename: raw.file_name ?? raw.filename ?? "unknown",
    status: raw.status ?? "processing",
    atsScore: raw.ats_score ?? raw.atsScore ?? undefined,
    resumeScore: raw.resume_score ?? raw.resumeScore ?? undefined,
    skills: raw.extracted_skills ?? raw.skills ?? [],
    structuredData: raw.structured_data ?? raw.structuredData ?? {},
    suggestions: Array.isArray(raw.suggestions)
      ? raw.suggestions.map((s: any, i: number) =>
          typeof s === "string"
            ? { id: `${raw.id ?? "resume"}_sugg_${i}`, category: "clarity" as const, message: s }
            : s
        )
      : [],
    uploadedAt: raw.created_at ?? raw.uploadedAt ?? new Date().toISOString(),
  };
}

export async function uploadResume(_file: File): Promise<Resume> {
  if (USE_MOCK) return delay({ ...mockResume, status: "processing" }, 400).then(() => delay(mockResume, 1200));
  const form = new FormData();
  form.append("file", _file);
  const res = await fetch(`${API_BASE}/api/v1/resumes/upload`, {
    method: "POST",
    headers: authHeaders(),
    body: form,
  });
  await assertOk(res, "Upload failed");
  const raw = await res.json();
  return mapResumeOut(raw);
}

export async function getResume(id: string): Promise<Resume> {
  if (USE_MOCK) return delay(mockResume);
  const res = await fetch(`${API_BASE}/api/v1/resumes/${id}`, { headers: authHeaders() });
  await assertOk(res, "Failed to fetch resume");
  const raw = await res.json();
  return mapResumeOut(raw);
}

export async function listResumes(): Promise<Resume[]> {
  if (USE_MOCK) return delay([mockResume]);
  const res = await fetch(`${API_BASE}/api/v1/resumes`, { headers: authHeaders() });
  await assertOk(res, "Failed to fetch resumes");
  return (await res.json()).map(mapResumeOut);
}

// ---- Engine 2: Job Match -------------------------------------------------

export async function matchJobs(resumeId: string, jobDescription?: string, jobMetadata?: Record<string, any>): Promise<JobMatch[]> {
  if (USE_MOCK) return delay(mockJobMatches, 700);
  if (!jobDescription?.trim()) return [];
  const res = await fetch(`${API_BASE}/api/v1/jobs/match`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({
      resume_id: resumeId,
      job_description: jobDescription,
      job_metadata: jobMetadata || {},
    }),
  });
  await assertOk(res, "Job match failed");
  const raw = await res.json();
  return [{
    id: `match_${resumeId}`,
    title: jobMetadata?.title ?? "Job match",
    company: jobMetadata?.company ?? "Company not specified",
    location: jobMetadata?.location ?? "Location not specified",
    matchScore: Math.round((raw.match_score ?? 0) * 100),
    visaFriendly: Boolean(jobMetadata?.visa_sponsorship),
    salaryFit: raw.hard_constraints_satisfied !== false,
    explanation: raw.explanation ?? "No explanation returned.",
    matchedSkills: raw.matched_skills ?? [],
    missingSkills: raw.missing_skills ?? [],
    recommendation: raw.recommendation ?? "possible_fit",
    hardConstraintsSatisfied: raw.hard_constraints_satisfied !== false,
  }];
}

// ---- Engine 3: Career Health ---------------------------------------------

export async function getCareerHealth(): Promise<CareerHealth> {
  if (USE_MOCK) return delay(mockCareerHealth, 500);
  const res = await fetch(`${API_BASE}/api/v1/career-health`, { headers: authHeaders() });
  await assertOk(res, "Failed to load career health");
  const raw = await res.json();
  const trend = raw.trend === "improving" ? "up" : raw.trend === "declining" ? "down" : "flat";
  const score = raw.career_health_score ?? 0;
  return {
    score,
    trend,
    trendDeltaPct: 0,
    weakAreas: raw.weak_areas ?? [],
    benchmarks: raw.benchmarks ?? {},
    recommendations: raw.recommendations ?? [],
    history: [{ label: "Current", score }],
  };
}

// ---- Engine 4: Career Copilot ---------------------------------------------

export async function getCopilotThread(conversationId?: string): Promise<CopilotMessage[]> {
  if (USE_MOCK) return delay(mockCopilotThread);
  if (!conversationId) return [];
  const res = await fetch(`${API_BASE}/api/v1/copilot/conversations/${conversationId}/messages`, {
    headers: authHeaders(),
  });
  await assertOk(res, "Failed to fetch copilot conversation");
  const raw = await res.json();
  return raw.map((message: any, index: number) => ({
    id: `${conversationId}_${index}`,
    role: message.role === "assistant" ? "assistant" : "user",
    content: message.content,
    createdAt: message.created_at,
    conversationId,
  }));
}

export async function sendCopilotMessage(content: string, conversationId?: string): Promise<CopilotMessage> {
  if (USE_MOCK) {
    return delay(
      {
        id: `m_${Date.now()}`,
        role: "assistant",
        content:
          "Here's what I'd focus on next: your ATS score dipped slightly on keyword coverage for backend roles. Want me to point out which bullet points to tighten?",
        createdAt: new Date().toISOString(),
        suggestedActions: ["Review resume suggestions", "Run job match"],
      },
      900
    );
  }
  const res = await fetch(`${API_BASE}/api/v1/copilot/message`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({
      message: content,
      conversation_id: conversationId,
    }),
  });
  await assertOk(res, "Copilot request failed");
  const raw = await res.json();
  return {
    id: `assistant_${Date.now()}`,
    role: "assistant",
    content: raw.reply,
    createdAt: new Date().toISOString(),
    conversationId: raw.conversation_id,
    suggestedActions: raw.suggested_actions ?? [],
  };
}

// ---- Applications / Progress ----------------------------------------------

export async function getApplications(): Promise<ApplicationStage[]> {
  if (USE_MOCK) return delay(mockApplications);
  const res = await fetch(`${API_BASE}/api/v1/applications`, { headers: authHeaders() });
  await assertOk(res, "Failed to load applications");
  return res.json();
}
