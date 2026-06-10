"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { apiFetch } from "@/lib/api-client";

export function LogoutButton() {
  const router = useRouter();
  const [pending, setPending] = useState(false);

  const handleLogout = async () => {
    setPending(true);
    try {
      await apiFetch<{ status: string }>("/api/auth/logout", { method: "POST" });
    } catch {
      // Session may already be gone; land on the login screen either way.
    } finally {
      router.push("/login");
    }
  };

  return (
    <button
      type="button"
      onClick={() => void handleLogout()}
      disabled={pending}
      className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-100 disabled:opacity-50"
    >
      {pending ? "Signing out..." : "Sign out"}
    </button>
  );
}
