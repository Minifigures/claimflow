import { OutcomeBadge } from "@/components/agent/outcome-badge";
import { formatCurrency, formatDate } from "@/lib/format";
import type { HistoryRow, HistoryStats } from "@/lib/types-agent";

interface ClaimHistoryTableProps {
  rows: HistoryRow[];
  stats: HistoryStats;
}

/** Claimant claim-history table plus the one-line stats summary. */
export function ClaimHistoryTable({ rows, stats }: ClaimHistoryTableProps) {
  return (
    <div>
      <p className="text-sm text-slate-600">
        {stats.total} prior claim{stats.total === 1 ? "" : "s"} · {stats.approved} approved ·{" "}
        {stats.rejected} rejected
      </p>

      {rows.length === 0 ? (
        <p className="mt-3 rounded-md border border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-center text-sm text-slate-500">
          No prior claims on record for this member.
        </p>
      ) : (
        <div className="mt-3 overflow-hidden rounded-md border border-slate-200">
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-slate-50">
              <tr>
                <th scope="col" className="px-3 py-2 text-left font-semibold text-slate-600">
                  Date
                </th>
                <th scope="col" className="px-3 py-2 text-left font-semibold text-slate-600">
                  Type
                </th>
                <th scope="col" className="px-3 py-2 text-left font-semibold text-slate-600">
                  Procedure
                </th>
                <th scope="col" className="px-3 py-2 text-right font-semibold text-slate-600">
                  Amount
                </th>
                <th scope="col" className="px-3 py-2 text-left font-semibold text-slate-600">
                  Outcome
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((row, index) => (
                <tr key={`${row.procedure_code}-${row.date_of_service ?? "unknown"}-${index}`}>
                  <td className="px-3 py-2 text-slate-700">
                    {row.date_of_service !== null ? formatDate(row.date_of_service) : "-"}
                  </td>
                  <td className="px-3 py-2 capitalize text-slate-700">{row.claim_type}</td>
                  <td className="px-3 py-2 font-mono text-slate-700">{row.procedure_code}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-slate-700">
                    {formatCurrency(row.billed_amount)}
                  </td>
                  <td className="px-3 py-2">
                    <OutcomeBadge outcome={row.outcome} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
