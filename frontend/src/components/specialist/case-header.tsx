import { ClaimStateBadge } from "@/components/claim-state-badge";
import { formatCurrency, formatDateTime } from "@/lib/format";
import type { CaseDetail } from "@/lib/types-specialist";

/** Compact claim summary card shared by both specialist case views. */
export function CaseHeader({ detail }: { detail: CaseDetail }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="font-mono text-base font-semibold text-slate-900">{detail.claim_ref}</h2>
          <p className="text-sm capitalize text-slate-500">
            {detail.claim_type} claim · {detail.claimant}
          </p>
        </div>
        <ClaimStateBadge state={detail.state} />
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-4">
        <div>
          <dt className="text-slate-500">Amount claimed</dt>
          <dd className="tabular-nums text-slate-900">{formatCurrency(detail.amount_claimed)}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Incident date</dt>
          <dd className="text-slate-900">{detail.incident_date ?? "Not provided"}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Procedure code</dt>
          <dd className="text-slate-900">{detail.procedure_code || "Not provided"}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Diagnosis code</dt>
          <dd className="text-slate-900">{detail.diagnosis_code || "Not provided"}</dd>
        </div>
        <div className="col-span-2 sm:col-span-4">
          <dt className="text-slate-500">Description</dt>
          <dd className="whitespace-pre-wrap text-slate-900">{detail.description}</dd>
        </div>
        <div className="col-span-2">
          <dt className="text-slate-500">Submitted</dt>
          <dd className="text-slate-900">{formatDateTime(detail.created_at)}</dd>
        </div>
        <div className="col-span-2">
          <dt className="text-slate-500">Last updated</dt>
          <dd className="text-slate-900">{formatDateTime(detail.updated_at)}</dd>
        </div>
      </dl>
    </section>
  );
}
