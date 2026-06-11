import type { ArtifactStatus, AuthenticityVerdict } from "@/lib/types";

/** Shared chip styling, reused by claim-state-badge and document chips. */
export const BADGE_BASE =
  "inline-flex items-center rounded-sm px-2 py-0.5 font-mono text-[11px] font-medium uppercase tracking-wide ring-1 ring-inset";

const REPORT_STYLES: Record<ArtifactStatus, string> = {
  pending: "bg-slate-50 text-slate-600 ring-slate-200",
  running: "bg-blue-50 text-blue-700 ring-blue-200",
  complete: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  failed: "bg-red-50 text-red-700 ring-red-200",
};

const VERDICT_STYLES: Record<AuthenticityVerdict, string> = {
  authentic: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  suspicious: "bg-amber-50 text-amber-700 ring-amber-200",
  likely_fraudulent: "bg-red-50 text-red-700 ring-red-200",
};

const VERDICT_LABELS: Record<AuthenticityVerdict, string> = {
  authentic: "Authentic",
  suspicious: "Suspicious",
  likely_fraudulent: "Likely fraudulent",
};

export function ReportStatusBadge({ status }: { status: ArtifactStatus | null }) {
  if (status === null) {
    return (
      <span className={`${BADGE_BASE} bg-slate-50 text-slate-500 ring-slate-200`}>Queued</span>
    );
  }
  return (
    <span className={`${BADGE_BASE} ${REPORT_STYLES[status]}`}>
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </span>
  );
}

export function VerdictBadge({ verdict }: { verdict: AuthenticityVerdict | null }) {
  if (verdict === null) {
    return (
      <span className={`${BADGE_BASE} bg-slate-50 text-slate-500 ring-slate-200`}>Awaiting</span>
    );
  }
  return (
    <span className={`${BADGE_BASE} ${VERDICT_STYLES[verdict]}`}>{VERDICT_LABELS[verdict]}</span>
  );
}
