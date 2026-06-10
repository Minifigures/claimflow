import { BADGE_BASE } from "@/components/status-badge";
import type { ClaimState } from "@/lib/types";

interface StateMeta {
  label: string;
  className: string;
}

/**
 * Shared label + color mapping for all 8 claim states.
 * Terminal states are green/red, "back to claimant" states are amber, and
 * in-flight review states stay in the neutral blue family.
 */
export const CLAIM_STATE_META: Record<ClaimState, StateMeta> = {
  SUBMITTED: {
    label: "Submitted",
    className: "bg-blue-50 text-blue-700 ring-blue-200",
  },
  IMAGING_REVIEW: {
    label: "Imaging review",
    className: "bg-sky-50 text-sky-700 ring-sky-200",
  },
  RETURNED_TO_CLAIMANT: {
    label: "Returned to you",
    className: "bg-amber-50 text-amber-700 ring-amber-200",
  },
  SPECIALIST_REVIEW: {
    label: "Specialist review",
    className: "bg-indigo-50 text-indigo-700 ring-indigo-200",
  },
  PENDING_FURTHER_TESTING: {
    label: "Further testing requested",
    className: "bg-amber-50 text-amber-700 ring-amber-200",
  },
  ADJUDICATION: {
    label: "Adjudication",
    className: "bg-violet-50 text-violet-700 ring-violet-200",
  },
  APPROVED: {
    label: "Approved",
    className: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  },
  REJECTED: {
    label: "Rejected",
    className: "bg-red-50 text-red-700 ring-red-200",
  },
};

const UNKNOWN_META: StateMeta = {
  label: "Unknown",
  className: "bg-slate-50 text-slate-500 ring-slate-200",
};

export function ClaimStateBadge({ state }: { state: ClaimState }) {
  const meta = CLAIM_STATE_META[state] ?? UNKNOWN_META;
  return <span className={`${BADGE_BASE} ${meta.className}`}>{meta.label}</span>;
}
