"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { register } from "@/lib/api";

export default function RegisterPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const { token } = await register(email, password, name);
      localStorage.setItem("afm_token", token);
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't create your account. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen flex items-center justify-center px-6">
      <div className="w-full max-w-sm">
        <span className="font-mono text-xs tracking-[0.3em] text-mint uppercase">Get started</span>
        <h1 className="font-display text-3xl font-semibold mt-2 mb-8">Create your account</h1>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <label className="flex flex-col gap-1.5 text-sm">
            Name
            <input
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="bg-panel panel-border rounded-md px-3 py-2.5 outline-none focus:border-signal"
              placeholder="Your name"
            />
          </label>
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
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="bg-panel panel-border rounded-md px-3 py-2.5 outline-none focus:border-signal"
              placeholder="At least 8 characters"
            />
          </label>
          {error && <p className="text-danger text-sm">{error}</p>}
          <button
            type="submit"
            disabled={loading}
            className="bg-signal text-ink font-semibold rounded-md py-2.5 mt-2 hover:brightness-110 transition disabled:opacity-60"
          >
            {loading ? "Creating account…" : "Create account"}
          </button>
        </form>
        <p className="text-muted text-sm mt-6">
          Already have an account?{" "}
          <Link href="/login" className="text-mint hover:underline">
            Sign in
          </Link>
        </p>
      </div>
    </main>
  );
}
