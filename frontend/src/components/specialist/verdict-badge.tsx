import { BADGE_BASE } from "@/components/status-badge";
import type { Recommendation } from "@/lib/types-specialist";

const RECOMMENDATION_STYLES: Record<Recommendation, string> = {
  SUPPORTS_CLAIM: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  INSUFFICIENT_EVIDENCE: "bg-amber-50 text-amber-700 ring-amber-200",
  REQUIRES_FURTHER_TESTING: "bg-blue-50 text-blue-700 ring-blue-200",
};

const RECOMMENDATION_LABELS: Record<Recommendation, string> = {
  SUPPORTS_CLAIM: "Supports claim",
  INSUFFICIENT_EVIDENCE: "Insufficient evidence",
  REQUIRES_FURTHER_TESTING: "Requires further testing",
};

/** Stage-2 recommendation verdict pill (shared by the queue and case views). */
export function RecommendationBadge({
  recommendation,
}: {
  recommendation: Recommendation | null;
}) {
  if (recommendation === null) {
    return (
      <span className={`${BADGE_BASE} bg-slate-50 text-slate-500 ring-slate-200`}>Awaiting</span>
    );
  }
  return (
    <span className={`${BADGE_BASE} ${RECOMMENDATION_STYLES[recommendation]}`}>
      {RECOMMENDATION_LABELS[recommendation]}
    </span>
  );
}

/** Mandatory-review flag cell, shared by both specialist queues. */
export function MandatoryReviewFlag({ required }: { required: boolean | null }) {
  if (required === true) {
    return (
      <span className={`${BADGE_BASE} bg-red-50 font-semibold text-red-700 ring-red-200`}>
        Required
      </span>
    );
  }
  return <span className="text-slate-400">Not required</span>;
}
