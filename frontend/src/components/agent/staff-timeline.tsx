import { formatDateTime } from "@/lib/format";
import type { ClaimAction, TimelineEntry } from "@/lib/types";

/**
 * Compact staff-facing timeline of workflow transitions. Unlike the claimant
 * timeline (claim-status-timeline.tsx) this renders every recorded transition
 * verbatim, including actor roles and internal notes.
 */

const ACTION_LABELS: Record<ClaimAction, string> = {
  submit: "Claim submitted",
  imaging_complete: "Imaging analysis complete",
  forward: "Forwarded to medical specialist",
  return_to_claimant: "Returned to claimant",
  resubmit: "Resubmitted by claimant",
  send_to_insurer: "Sent to insurer",
  request_further_testing: "Further testing requested",
  approve: "Claim approved",
  reject: "Claim rejected",
};

const ROLE_LABELS: Record<string, string> = {
  claimant: "Claimant",
  imaging_specialist: "Imaging specialist",
  medical_specialist: "Medical specialist",
  insurance_agent: "Insurance agent",
  system: "System",
};

function dotClass(action: ClaimAction): string {
  if (action === "approve") {
    return "border-emerald-500 bg-emerald-500";
  }
  if (action === "reject") {
    return "border-red-500 bg-red-500";
  }
  if (action === "return_to_claimant" || action === "request_further_testing") {
    return "border-amber-500 bg-amber-500";
  }
  return "border-blue-600 bg-blue-600";
}

export function StaffTimeline({ entries }: { entries: TimelineEntry[] }) {
  if (entries.length === 0) {
    return <p className="text-sm text-slate-500">No workflow events recorded yet.</p>;
  }

  return (
    <ol>
      {entries.map((entry, index) => (
        <li
          key={`${entry.action}-${entry.created_at}-${index}`}
          className="relative flex gap-3 pb-5 last:pb-0"
        >
          {index < entries.length - 1 ? (
            <span
              aria-hidden="true"
              className="absolute left-[5px] top-4 h-full w-0.5 bg-slate-200"
            />
          ) : null}
          <span
            aria-hidden="true"
            className={`relative mt-1 h-3 w-3 shrink-0 rounded-full border-2 ${dotClass(entry.action)}`}
          />
          <div className="min-w-0">
            <p className="text-sm font-medium text-slate-900">
              {ACTION_LABELS[entry.action] ?? entry.action}
            </p>
            <p className="text-xs text-slate-500">
              {ROLE_LABELS[entry.actor_role] ?? entry.actor_role} ·{" "}
              {formatDateTime(entry.created_at)}
            </p>
            {entry.note !== null && entry.note.length > 0 ? (
              <p className="mt-1 text-sm text-slate-600">{entry.note}</p>
            ) : null}
          </div>
        </li>
      ))}
    </ol>
  );
}
