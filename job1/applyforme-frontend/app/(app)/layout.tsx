"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";

export default function AppShellLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [hasToken, setHasToken] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem("afm_token");
    if (!token) {
      router.replace("/login");
      return;
    }
    setHasToken(true);
  }, [router]);

  if (!hasToken) {
    return (
      <main className="min-h-screen grid place-items-center px-6">
        <p className="font-mono text-sm text-muted">Checking session...</p>
      </main>
    );
  }

  return (
    <div className="flex">
      <Sidebar />
      <main className="flex-1 min-h-screen px-8 py-8 max-w-6xl">{children}</main>
    </div>
  );
}
