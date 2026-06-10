import { BADGE_BASE } from "@/components/status-badge";

/**
 * Outcome badge for claimant-history rows and precedent (similar-case) outcomes.
 * Outcomes arrive as free-form strings ("approved", "APPROVED", "rejected", ...),
 * so the styling is keyed off a case-insensitive match.
 */
export function OutcomeBadge({ outcome }: { outcome: string }) {
  const normalized = outcome.toLowerCase();
  const className =
    normalized === "approved"
      ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
      : normalized === "rejected"
        ? "bg-red-50 text-red-700 ring-red-200"
        : "bg-slate-100 text-slate-600 ring-slate-300";
  const label = normalized.charAt(0).toUpperCase() + normalized.slice(1);
  return <span className={`${BADGE_BASE} ${className}`}>{label}</span>;
}
