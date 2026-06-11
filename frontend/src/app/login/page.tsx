"use client";

import Link from "next/link";
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

const DEMO_ACCOUNTS: ReadonlyArray<{
  label: string;
  tag: string;
  email: string;
  hint: string;
}> = [
  { label: "Claimant", tag: "default", email: "claimant@demo.ca", hint: "submit & track claims" },
  {
    label: "Imaging specialist",
    tag: "portal 1",
    email: "imaging@demo.ca",
    hint: "review forensic signals",
  },
  {
    label: "Medical specialist",
    tag: "portal 2",
    email: "specialist@demo.ca",
    hint: "weigh the evidence",
  },
  {
    label: "Insurance agent",
    tag: "portal 3",
    email: "agent@demo.ca",
    hint: "make the final call",
  },
];

const PIPELINE: ReadonlyArray<{ numeral: string; line: string }> = [
  { numeral: "01", line: "Imaging analysis — CNN + forensics" },
  { numeral: "02", line: "Medical review — LLM evidence note" },
  { numeral: "03", line: "Adjudication — history + precedents" },
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
    <div className="flex min-h-screen">
      {/* Ink story panel */}
      <aside className="ink-panel relative hidden w-[42%] flex-col justify-between p-10 lg:flex">
        <Link
          href="/"
          className="font-display text-2xl font-semibold italic tracking-tight text-slate-50"
        >
          ClaimFlow
        </Link>
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.3em] text-blue-300">
            One auditable workflow
          </p>
          <h2 className="mt-4 font-display text-3xl font-medium leading-snug text-slate-50">
            Models draft the evidence.
            <br />
            People own the outcome.
          </h2>
          <ul className="mt-8 space-y-4 border-l border-blue-400/30 pl-5">
            {PIPELINE.map((stage) => (
              <li key={stage.numeral} className="flex items-baseline gap-3">
                <span className="font-mono text-xs text-blue-300">{stage.numeral}</span>
                <span className="text-sm text-blue-100">{stage.line}</span>
              </li>
            ))}
          </ul>
        </div>
        <p className="font-mono text-[11px] uppercase tracking-wide text-blue-300/80">
          hash-chained audit · keyless fallbacks · not a clinical device
        </p>
      </aside>

      {/* Paper form panel */}
      <main className="paper-grid flex flex-1 items-center justify-center px-4 py-12">
        <div className="w-full max-w-md">
          <div className="anim-rise" style={{ "--rise-delay": "0ms" } as React.CSSProperties}>
            <p className="font-display text-xl font-semibold italic text-blue-900 lg:hidden">
              ClaimFlow
            </p>
            <h1 className="mt-2 font-display text-3xl font-medium text-slate-900">Sign in</h1>
            <p className="mt-1 text-sm text-slate-500">
              Medical claims processing portal — pick a demo role or use credentials.
            </p>
          </div>

          <div
            className="anim-rise mt-6 grid grid-cols-1 gap-2 sm:grid-cols-2"
            style={{ "--rise-delay": "90ms" } as React.CSSProperties}
          >
            {DEMO_ACCOUNTS.map((account) => (
              <button
                key={account.email}
                type="button"
                disabled={pending}
                onClick={() => void signIn(account.email, DEMO_PASSWORD)}
                className="group rounded-sm border border-slate-300 bg-white px-4 py-3 text-left transition-all hover:-translate-y-0.5 hover:border-blue-700 hover:shadow-sm disabled:opacity-50"
              >
                <span className="flex items-baseline justify-between gap-2">
                  <span className="text-sm font-semibold text-slate-800 group-hover:text-blue-900">
                    {account.label}
                  </span>
                  <span className="shrink-0 font-mono text-[10px] uppercase tracking-wide text-blue-700">
                    {account.tag}
                  </span>
                </span>
                <span className="mt-0.5 block font-mono text-[11px] text-slate-500">
                  {account.hint}
                </span>
              </button>
            ))}
          </div>

          <div
            className="anim-rise my-6 flex items-center gap-3"
            style={{ "--rise-delay": "180ms" } as React.CSSProperties}
          >
            <span className="h-px flex-1 bg-slate-200" />
            <span className="font-mono text-[11px] uppercase tracking-widest text-slate-400">
              or with credentials
            </span>
            <span className="h-px flex-1 bg-slate-200" />
          </div>

          <form
            onSubmit={handleSubmit}
            className="anim-rise rounded-sm border border-slate-200 bg-white p-6 shadow-sm"
            style={{ "--rise-delay": "270ms" } as React.CSSProperties}
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
              className="mt-1 block w-full rounded-sm border border-slate-300 bg-slate-50 px-3 py-2 text-sm text-slate-900 focus:border-blue-700 focus:bg-white focus:outline-none focus:ring-1 focus:ring-blue-700"
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
              className="mt-1 block w-full rounded-sm border border-slate-300 bg-slate-50 px-3 py-2 text-sm text-slate-900 focus:border-blue-700 focus:bg-white focus:outline-none focus:ring-1 focus:ring-blue-700"
            />

            {error ? (
              <p role="alert" className="mt-4 text-sm text-red-700">
                {error}
              </p>
            ) : null}

            <button
              type="submit"
              disabled={pending}
              className="mt-6 w-full rounded-sm bg-blue-800 px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-blue-900 disabled:opacity-50"
            >
              {pending ? "Signing in..." : "Sign in"}
            </button>
          </form>

          <p
            className="anim-rise mt-6 text-center font-mono text-[11px] text-slate-400"
            style={{ "--rise-delay": "360ms" } as React.CSSProperties}
          >
            all demo roles share the password {DEMO_PASSWORD}
          </p>
        </div>
      </main>
    </div>
  );
}
