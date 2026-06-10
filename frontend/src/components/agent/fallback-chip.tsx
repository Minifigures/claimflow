import { BADGE_BASE } from "@/components/status-badge";

/** Amber provenance chip shown whenever an artifact was produced by the deterministic
 * fallback path instead of the model (fallback_reason is set). */
export function FallbackChip({ reason }: { reason: string | null }) {
  if (reason === null) {
    return null;
  }
  return (
    <span
      title={`Fallback reason: ${reason}`}
      className={`${BADGE_BASE} bg-amber-50 text-amber-800 ring-amber-200`}
    >
      Deterministic fallback
    </span>
  );
}
