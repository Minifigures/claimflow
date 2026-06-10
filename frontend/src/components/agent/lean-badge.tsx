import { BADGE_BASE } from "@/components/status-badge";
import type { RecommendationLean } from "@/lib/types-agent";

const LEAN_META: Record<RecommendationLean, { label: string; className: string }> = {
  LEAN_APPROVE: {
    label: "Lean approve",
    className: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  },
  LEAN_REJECT: {
    label: "Lean reject",
    className: "bg-red-50 text-red-700 ring-red-200",
  },
  NO_CLEAR_LEAN: {
    label: "No clear lean",
    className: "bg-slate-100 text-slate-600 ring-slate-300",
  },
};

export function LeanBadge({ lean }: { lean: RecommendationLean | null }) {
  if (lean === null) {
    return (
      <span className={`${BADGE_BASE} bg-slate-50 text-slate-400 ring-slate-200`}>Awaiting</span>
    );
  }
  const meta = LEAN_META[lean];
  return <span className={`${BADGE_BASE} ${meta.className}`}>{meta.label}</span>;
}
