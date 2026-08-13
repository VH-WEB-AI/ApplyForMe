import Link from "next/link";

export default function LandingPage() {
  return (
    <main className="min-h-screen bg-grid-fade bg-grid flex flex-col items-center justify-center px-6 text-center">
      <span className="font-mono text-xs tracking-[0.3em] text-mint uppercase mb-4">
        Career Command Center
      </span>
      <h1 className="font-display text-4xl md:text-6xl font-semibold max-w-3xl leading-tight">
        Every signal in your job search, on one instrument panel.
      </h1>
      <p className="font-body text-muted max-w-xl mt-5 text-base md:text-lg">
        Resume score, job match, career health, and an AI copilot that reads
        context from all three — routed through one orchestrator, never a
        raw model call.
      </p>
      <div className="flex gap-4 mt-9">
        <Link
          href="/register"
          className="bg-signal text-ink font-semibold px-6 py-3 rounded-md hover:brightness-110 transition"
        >
          Create account
        </Link>
        <Link
          href="/login"
          className="panel-border text-ivory font-semibold px-6 py-3 rounded-md hover:bg-panel transition"
        >
          Sign in
        </Link>
      </div>
    </main>
  );
}
