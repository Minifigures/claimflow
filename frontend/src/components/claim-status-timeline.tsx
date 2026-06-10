import type { ReactNode } from "react";

import { formatDateTime } from "@/lib/format";
import type { ClaimAction, ClaimState, TimelineEntry } from "@/lib/types";

/**
 * PRIVACY RULE (deliberate product decision): this component is rendered in
 * the claimant portal and must NEVER surface authenticity/fraud information
 * (authenticity_verdict, authenticity_risk, fraud signals,
 * requires_mandatory_review), even though the owner-scoped API returns those
 * fields. Claimants only see workflow progress, reviewer notes from Decision
 * rows, and neutral "under review by our imaging team" copy. No fraud
 * language anywhere in claimant-facing UI.
 */

type StageStatus = "complete" | "current" | "attention" | "upcoming";

interface StageView {
  label: string;
  status: StageStatus;
  timestamp: string | null;
  detail: string | null;
  tone: "neutral" | "green" | "red" | "amber";
}

interface ClaimStatusTimelineProps {
  state: ClaimState;
  entries: TimelineEntry[];
  /** Rendered inside the amber callout when the claim is back with the claimant. */
  resubmitPanel?: ReactNode;
}

const RETURNED_STATES: ReadonlySet<ClaimState> = new Set([
  "RETURNED_TO_CLAIMANT",
  "PENDING_FURTHER_TESTING",
]);

/** Index of the active assessment stage for each claim state. */
const STAGE_INDEX: Record<ClaimState, number> = {
  SUBMITTED: 1,
  IMAGING_REVIEW: 1,
  RETURNED_TO_CLAIMANT: 1,
  SPECIALIST_REVIEW: 2,
  PENDING_FURTHER_TESTING: 2,
  ADJUDICATION: 3,
  APPROVED: 4,
  REJECTED: 4,
};

function latestByAction(entries: TimelineEntry[], actions: ClaimAction[]): TimelineEntry | null {
  for (let i = entries.length - 1; i >= 0; i -= 1) {
    if (actions.includes(entries[i].action)) {
      return entries[i];
    }
  }
  return null;
}

function buildStages(state: ClaimState, entries: TimelineEntry[]): StageView[] {
  const activeIndex = STAGE_INDEX[state] ?? 1;
  const returned = RETURNED_STATES.has(state);
  const terminal = state === "APPROVED" || state === "REJECTED";

  const submitted = latestByAction(entries, ["submit", "resubmit"]);
  const forwarded = latestByAction(entries, ["forward"]);
  const sentToInsurer = latestByAction(entries, ["send_to_insurer"]);
  const decided = latestByAction(entries, ["approve", "reject"]);
  const returnedEntry = latestByAction(entries, ["return_to_claimant", "request_further_testing"]);

  const statusFor = (index: number): StageStatus => {
    if (index < activeIndex || terminal) {
      return "complete";
    }
    if (index === activeIndex) {
      return returned ? "attention" : "current";
    }
    return "upcoming";
  };

  const stages: StageView[] = [
    {
      label: "Submitted",
      status: statusFor(0),
      timestamp: submitted?.created_at ?? null,
      detail: submitted?.action === "resubmit" ? "Resubmitted with additional information" : null,
      tone: "neutral",
    },
    {
      label: "Imaging analysis",
      status: statusFor(1),
      timestamp: state === "SUBMITTED" || state === "IMAGING_REVIEW" ? null : (forwarded?.created_at ?? null),
      detail:
        state === "SUBMITTED" || state === "IMAGING_REVIEW"
          ? "Your imaging is under review by our imaging team."
          : null,
      tone: "neutral",
    },
    {
      label: "Specialist review",
      status: statusFor(2),
      timestamp:
        state === "SPECIALIST_REVIEW" ? null : (sentToInsurer?.created_at ?? null),
      detail:
        state === "SPECIALIST_REVIEW" ? "A medical specialist is reviewing your claim." : null,
      tone: "neutral",
    },
    {
      label: "Insurance adjudication",
      status: statusFor(3),
      timestamp: state === "ADJUDICATION" ? null : (decided?.created_at ?? null),
      detail: state === "ADJUDICATION" ? "Your claim is with our insurance team." : null,
      tone: "neutral",
    },
    {
      label:
        state === "APPROVED"
          ? "Decision: approved"
          : state === "REJECTED"
            ? "Decision: rejected"
            : "Decision",
      status: terminal ? "complete" : "upcoming",
      timestamp: terminal ? (decided?.created_at ?? null) : null,
      detail: terminal ? (decided?.note ?? null) : null,
      tone: state === "APPROVED" ? "green" : state === "REJECTED" ? "red" : "neutral",
    },
  ];

  if (returned) {
    // Surface the reviewer's note on the stage that sent the claim back.
    stages[activeIndex] = {
      ...stages[activeIndex],
      detail: returnedEntry?.note ?? null,
      tone: "amber",
    };
  }

  return stages;
}

const DOT_STYLES: Record<StageStatus, string> = {
  complete: "border-emerald-500 bg-emerald-500",
  current: "border-blue-600 bg-blue-600",
  attention: "border-amber-500 bg-amber-500",
  upcoming: "border-slate-300 bg-white",
};

function stageDotClass(stage: StageView): string {
  if (stage.status === "complete" && stage.tone === "red") {
    return "border-red-500 bg-red-500";
  }
  return DOT_STYLES[stage.status];
}

function stageLabelClass(stage: StageView): string {
  if (stage.tone === "green") {
    return "text-emerald-700";
  }
  if (stage.tone === "red") {
    return "text-red-700";
  }
  if (stage.status === "upcoming") {
    return "text-slate-400";
  }
  return "text-slate-900";
}

export function ClaimStatusTimeline({ state, entries, resubmitPanel }: ClaimStatusTimelineProps) {
  const stages = buildStages(state, entries);
  const returned = RETURNED_STATES.has(state);
  const returnNote = returned
    ? (latestByAction(entries, ["return_to_claimant", "request_further_testing"])?.note ?? null)
    : null;

  return (
    <div>
      <ol className="space-y-0">
        {stages.map((stage, index) => (
          <li key={stage.label} className="relative flex gap-3 pb-6 last:pb-0">
            {index < stages.length - 1 ? (
              <span
                aria-hidden="true"
                className="absolute left-[7px] top-5 h-full w-0.5 bg-slate-200"
              />
            ) : null}
            <span
              aria-hidden="true"
              className={`relative mt-1 h-4 w-4 shrink-0 rounded-full border-2 ${stageDotClass(stage)}`}
            />
            <div className="min-w-0">
              <p className={`text-sm font-medium ${stageLabelClass(stage)}`}>{stage.label}</p>
              {stage.timestamp !== null ? (
                <p className="text-xs text-slate-500">{formatDateTime(stage.timestamp)}</p>
              ) : null}
              {stage.detail !== null ? (
                <p
                  className={`mt-1 text-sm ${stage.tone === "amber" ? "text-amber-800" : "text-slate-600"}`}
                >
                  {stage.detail}
                </p>
              ) : null}
            </div>
          </li>
        ))}
      </ol>

      {returned ? (
        <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-4">
          <p className="text-sm font-semibold text-amber-900">
            {state === "PENDING_FURTHER_TESTING"
              ? "Further testing requested"
              : "Your claim was returned"}
          </p>
          <p className="mt-1 text-sm text-amber-800">
            {returnNote ??
              "A reviewer sent this claim back to you. Please add the requested information and resubmit."}
          </p>
          {resubmitPanel !== undefined ? <div className="mt-4">{resubmitPanel}</div> : null}
        </div>
      ) : null}
    </div>
  );
}
