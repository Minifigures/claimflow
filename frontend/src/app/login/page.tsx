"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { FormEvent } from "react";

import { ApiError, apiFetch } from "@/lib/api-client";
import type { Role, UserOut } from "@/lib/types";

const ROLE_HOME: Record<Role, string> = {
  claimant: "/claimant",
  imaging_specialist: "/imaging",
  medical_specialist: "/specialist",
  insurance_agent: "/agent",
};

const DEMO_PASSWORD = "demo1234";

const DEMO_ACCOUNTS: ReadonlyArray<{ label: string; email: string }> = [
  { label: "Claimant", email: "claimant@demo.ca" },
  { label: "Imaging specialist", email: "imaging@demo.ca" },
  { label: "Medical specialist", email: "specialist@demo.ca" },
  { label: "Insurance agent", email: "agent@demo.ca" },
];

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const signIn = async (signInEmail: string, signInPassword: string) => {
    setPending(true);
    setError(null);
    try {
      const user = await apiFetch<UserOut>("/api/auth/login", {
        method: "POST",
        body: { email: signInEmail, password: signInPassword },
      });
      router.push(ROLE_HOME[user.role] ?? "/login");
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Unable to reach the API.");
      setPending(false);
    }
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void signIn(email, password);
  };

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <p className="text-xs font-semibold uppercase tracking-widest text-blue-700">
            ClaimFlow
          </p>
          <h1 className="mt-1 text-2xl font-semibold text-slate-900">Sign in</h1>
          <p className="mt-1 text-sm text-slate-500">Medical claims processing portal</p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm"
        >
          <label htmlFor="email" className="block text-sm font-medium text-slate-700">
            Email
          </label>
          <input
            id="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-blue-600 focus:outline-none focus:ring-1 focus:ring-blue-600"
          />

          <label htmlFor="password" className="mt-4 block text-sm font-medium text-slate-700">
            Password
          </label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-blue-600 focus:outline-none focus:ring-1 focus:ring-blue-600"
          />

          {error ? (
            <p role="alert" className="mt-4 text-sm text-red-700">
              {error}
            </p>
          ) : null}

          <button
            type="submit"
            disabled={pending}
            className="mt-6 w-full rounded-md bg-blue-700 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-blue-800 disabled:opacity-50"
          >
            {pending ? "Signing in..." : "Sign in"}
          </button>
        </form>

        <div className="mt-6">
          <p className="text-center text-xs font-medium uppercase tracking-wide text-slate-400">
            Demo accounts
          </p>
          <div className="mt-3 grid grid-cols-2 gap-2">
            {DEMO_ACCOUNTS.map((account) => (
              <button
                key={account.email}
                type="button"
                disabled={pending}
                onClick={() => void signIn(account.email, DEMO_PASSWORD)}
                className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 transition-colors hover:bg-slate-100 disabled:opacity-50"
              >
                Sign in as {account.label}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
