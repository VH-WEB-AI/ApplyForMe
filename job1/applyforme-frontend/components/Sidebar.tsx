"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

const NAV = [
  { href: "/dashboard", label: "Dashboard", glyph: "◈" },
  { href: "/resume", label: "Resume", glyph: "▤" },
  { href: "/jobs", label: "Job Match", glyph: "◎" },
  { href: "/career-health", label: "Career Health", glyph: "♥" },
  { href: "/copilot", label: "Copilot", glyph: "◆" },
  { href: "/progress", label: "Progress", glyph: "▸" },
];

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();

  function handleSignOut() {
    localStorage.removeItem("afm_token");
    router.push("/login");
  }

  return (
    <aside className="w-60 shrink-0 bg-panel panel-border border-y-0 border-l-0 min-h-screen flex flex-col">
      <div className="px-5 py-6">
        <span className="font-mono text-[10px] tracking-[0.3em] text-mint uppercase">ApplyForMe</span>
        <p className="font-display font-semibold text-lg leading-tight mt-1">Command Center</p>
      </div>
      <nav className="flex-1 px-3 flex flex-col gap-1">
        {NAV.map((item) => {
          const active = pathname === item.href;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-3 px-3 py-2.5 rounded-md text-sm font-medium transition ${
                active ? "bg-panel-raised text-ivory" : "text-muted hover:text-ivory hover:bg-panel-raised/50"
              }`}
            >
              <span className={`font-mono ${active ? "text-signal" : "text-rail"}`} aria-hidden>
                {item.glyph}
              </span>
              {item.label}
            </Link>
          );
        })}
      </nav>
      <div className="px-3 py-4 panel-border border-x-0 border-b-0">
        <button
          onClick={handleSignOut}
          className="w-full text-left px-3 py-2 text-sm text-muted hover:text-ivory rounded-md hover:bg-panel-raised/50 transition"
        >
          Sign out
        </button>
      </div>
    </aside>
  );
}
