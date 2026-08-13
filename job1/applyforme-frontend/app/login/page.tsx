"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { login } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const { token } = await login(email, password);
      localStorage.setItem("afm_token", token);
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't sign you in. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen flex items-center justify-center px-6">
      <div className="w-full max-w-sm">
        <span className="font-mono text-xs tracking-[0.3em] text-mint uppercase">Sign in</span>
        <h1 className="font-display text-3xl font-semibold mt-2 mb-8">Welcome back</h1>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <label className="flex flex-col gap-1.5 text-sm">
            Email
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="bg-panel panel-border rounded-md px-3 py-2.5 outline-none focus:border-signal"
              placeholder="you@example.com"
            />
          </label>
          <label className="flex flex-col gap-1.5 text-sm">
            Password
            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="bg-panel panel-border rounded-md px-3 py-2.5 outline-none focus:border-signal"
              placeholder="••••••••"
            />
          </label>
          {error && <p className="text-danger text-sm">{error}</p>}
          <button
            type="submit"
            disabled={loading}
            className="bg-signal text-ink font-semibold rounded-md py-2.5 mt-2 hover:brightness-110 transition disabled:opacity-60"
          >
            {loading ? "Signing in…" : "Sign in"}
          </button>
        </form>
        <p className="text-muted text-sm mt-6">
          New here?{" "}
          <Link href="/register" className="text-mint hover:underline">
            Create an account
          </Link>
        </p>
      </div>
    </main>
  );
}
