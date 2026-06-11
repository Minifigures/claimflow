import Link from "next/link";

const METRICS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "94.2%", label: "modality test accuracy" },
  { value: "253", label: "automated tests" },
  { value: "3", label: "human decision gates" },
  { value: "0", label: "unaudited actions" },
];

const STAGES: ReadonlyArray<{ numeral: string; title: string; detail: string }> = [
  {
    numeral: "01",
    title: "Imaging analysis",
    detail:
      "Trained CNNs classify the study and screen for tampering; a specialist reviews every finding.",
  },
  {
    numeral: "02",
    title: "Medical review",
    detail:
      "An LLM drafts the evidence note across all documents; the medical specialist decides what it means.",
  },
  {
    numeral: "03",
    title: "Adjudication",
    detail:
      "Claim history and anonymized precedents inform the agent's call; the decision and email commit atomically.",
  },
];

export default function LandingPage() {
  return (
    <div className="paper-grid relative flex min-h-screen flex-col overflow-hidden">
      <div
        aria-hidden
        className="pointer-events-none absolute -right-40 -top-40 h-[34rem] w-[34rem] rounded-full bg-blue-100 opacity-60 blur-3xl"
      />

      <header className="relative mx-auto flex w-full max-w-6xl items-center justify-between px-6 py-6">
        <p className="font-display text-2xl font-semibold italic tracking-tight text-blue-900">
          ClaimFlow
        </p>
        <Link
          href="/login"
          className="rounded-sm border border-slate-300 bg-white px-4 py-2 font-mono text-xs uppercase tracking-widest text-slate-700 transition-colors hover:border-blue-700 hover:text-blue-800"
        >
          Sign in
        </Link>
      </header>

      <main className="relative mx-auto flex w-full max-w-6xl flex-1 flex-col justify-center px-6 pb-16">
        <p
          className="anim-rise font-mono text-xs uppercase tracking-[0.3em] text-blue-700"
          style={{ "--rise-delay": "0ms" } as React.CSSProperties}
        >
          Medical claims · human-in-the-loop
        </p>
        <h1
          className="anim-rise mt-5 max-w-3xl font-display text-5xl font-medium leading-[1.05] tracking-tight text-slate-900 sm:text-7xl"
          style={{ "--rise-delay": "90ms" } as React.CSSProperties}
        >
          Every claim, <span className="italic text-blue-800">examined.</span>
        </h1>
        <p
          className="anim-rise mt-6 max-w-xl text-lg leading-relaxed text-slate-600"
          style={{ "--rise-delay": "180ms" } as React.CSSProperties}
        >
          Three machine-assisted analysis stages, three human decisions, one hash-chained audit
          trail. Models draft the evidence; people own the outcome.
        </p>

        <div
          className="anim-rise mt-10 flex flex-wrap items-center gap-4"
          style={{ "--rise-delay": "270ms" } as React.CSSProperties}
        >
          <Link
            href="/login"
            className="rounded-sm bg-blue-800 px-6 py-3 text-sm font-semibold text-white shadow-sm transition-all hover:-translate-y-0.5 hover:bg-blue-900 hover:shadow-md"
          >
            Enter the demo
          </Link>
          <p className="font-mono text-xs text-slate-500">
            four roles · one-click demo accounts · no signup
          </p>
        </div>

        <div
          className="anim-rise mt-16 grid max-w-3xl grid-cols-2 gap-px overflow-hidden rounded-sm border border-slate-200 bg-slate-200 sm:grid-cols-4"
          style={{ "--rise-delay": "360ms" } as React.CSSProperties}
        >
          {METRICS.map((metric) => (
            <div key={metric.label} className="bg-white px-4 py-4">
              <p className="font-display text-2xl font-semibold text-blue-900">{metric.value}</p>
              <p className="mt-1 font-mono text-[11px] uppercase tracking-wide text-slate-500">
                {metric.label}
              </p>
            </div>
          ))}
        </div>

        <div
          className="anim-rise mt-14 grid max-w-5xl gap-8 sm:grid-cols-3"
          style={{ "--rise-delay": "450ms" } as React.CSSProperties}
        >
          {STAGES.map((stage) => (
            <div key={stage.numeral} className="border-t-2 border-blue-800 pt-4">
              <p className="font-mono text-xs text-slate-400">{stage.numeral}</p>
              <h2 className="mt-1 font-display text-xl font-medium text-slate-900">
                {stage.title}
              </h2>
              <p className="mt-2 text-sm leading-relaxed text-slate-600">{stage.detail}</p>
            </div>
          ))}
        </div>
      </main>

      <footer className="relative border-t border-slate-200 bg-slate-50/80">
        <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-2 px-6 py-4">
          <p className="font-mono text-[11px] uppercase tracking-wide text-slate-500">
            Prototype for evaluation · not a clinical device
          </p>
          <p className="font-mono text-[11px] text-slate-400">
            trained CNNs · provider-pluggable LLM · hash-chained audit
          </p>
        </div>
      </footer>
    </div>
  );
}
