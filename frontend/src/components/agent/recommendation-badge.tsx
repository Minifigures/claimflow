import { BADGE_BASE } from "@/components/status-badge";
import type { SpecialistRecommendation } from "@/lib/types-agent";

const RECOMMENDATION_META: Record<
  SpecialistRecommendation,
  { label: string; className: string }
> = {
  SUPPORTS_CLAIM: {
    label: "Supports claim",
    className: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  },
  INSUFFICIENT_EVIDENCE: {
    label: "Insufficient evidence",
    className: "bg-amber-50 text-amber-700 ring-amber-200",
  },
  REQUIRES_FURTHER_TESTING: {
    label: "Further testing required",
    className: "bg-sky-50 text-sky-700 ring-sky-200",
  },
};

export function RecommendationBadge({
  recommendation,
}: {
  recommendation: SpecialistRecommendation | null;
}) {
  if (recommendation === null) {
    return (
      <span className={`${BADGE_BASE} bg-slate-50 text-slate-400 ring-slate-200`}>Awaiting</span>
    );
  }
  const meta = RECOMMENDATION_META[recommendation];
  return <span className={`${BADGE_BASE} ${meta.className}`}>{meta.label}</span>;
}
