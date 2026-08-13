# ApplyForMe — Career Command Center (Frontend)

Next.js (App Router) + TypeScript + Tailwind frontend for the ApplyForMe
backend. Matches the four engines documented in the backend README:
Resume Intelligence, Job Match, Career Health, Career Copilot.

## Design

"Career Command Center" — an instrument-panel aesthetic (arc gauges,
mono-spaced data, control-room palette) rather than a generic dashboard
template, since the whole point of the product is reading several live
signals at a glance.

- **Palette**: ink `#0A1412`, panel `#101C1A`, signal `#FF8A3D`, mint `#5EEAD4`
- **Type**: Space Grotesk (display), Inter (body), IBM Plex Mono (data/labels)
- **Signature element**: `components/Gauge.tsx` — the cockpit-style score dial
  used on the dashboard, resume, and career-health pages

## Run it

```bash
npm install
npm run dev
```

Visit http://localhost:3000. Ships pointed at mock data out of the box —
you can click through the whole app with no backend running.

## Connect the real backend

Everything the app knows about the API lives in **one file**:
`lib/api.ts`. It already contains a real `fetch()` implementation for
every endpoint listed in the backend README (`/api/v1/auth/login`,
`/api/v1/resumes/upload`, `/api/v1/jobs/match`, `/api/v1/career-health`,
`/api/v1/copilot/message`, etc.) — it's just not active yet.

To go live:

1. `cp .env.example .env.local` and set `NEXT_PUBLIC_API_BASE_URL` to
   your running FastAPI instance (e.g. `http://localhost:8000`).
2. In `lib/api.ts`, flip `const USE_MOCK = true;` to `false`.

No other file changes — every page calls the functions in `lib/api.ts`,
never `fetch` directly, so the swap is one line.

**The frontend never calls the LLM directly** — it only ever talks to
your API Gateway, which is what routes through the AI Orchestrator.

## Structure

```
app/
  page.tsx              landing page
  login/, register/     auth
  (app)/                authenticated shell (sidebar layout)
    dashboard/           instrument-cluster overview
    resume/               Engine 1 — upload, ATS score, suggestions
    jobs/                 Engine 2 — job matches + explanations
    career-health/        Engine 3 — aggregate score, trend, recommendations
    copilot/               Engine 4 — RAG chat
    progress/              application pipeline tracker
components/
  Sidebar.tsx, Gauge.tsx
lib/
  api.ts        the one API seam (mock <-> real)
  types.ts      shapes mirroring backend response envelopes
  mock-data.ts  fixtures used while USE_MOCK = true
```

## Still to wire up when you connect the real backend

- Resume upload progress via polling `GET /api/v1/resumes/{id}` for
  status transitions (`processing` → `scored`) — currently simulated
  with a timeout in mock mode.
- Token refresh flow (access + refresh tokens) — currently a single
  mock token in `localStorage`.
- Route protection / redirect-to-login for unauthenticated users hitting
  `(app)/*` routes — not yet implemented, add a middleware or a client
  check against the stored token.
