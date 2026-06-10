import { ArtifactPanel } from "@/components/agent/artifact-panel";
import { RecommendationBadge } from "@/components/agent/recommendation-badge";
import {
  formatConfidence,
  parseRecommendationPayload,
  type RecommendationNoteOut,
} from "@/lib/types-agent";

/** Stage-2 view: recommendation enum badge, summary, and identified gaps. */
export function SpecialistPanel({ note }: { note: RecommendationNoteOut | null }) {
  const payload = parseRecommendationPayload(note?.payload ?? null);

  return (
    <ArtifactPanel
      title="Specialist recommendation"
      artifact={note}
      emptyCopy="No specialist recommendation has been recorded for this claim."
    >
      {note !== null ? (
        <div className="space-y-3 text-sm">
          <div className="flex flex-wrap items-center gap-3">
            <RecommendationBadge recommendation={note.recommendation} />
            {note.confidence !== null ? (
              <span className="text-slate-500">Confidence {formatConfidence(note.confidence)}</span>
            ) : null}
          </div>
          {payload.summary !== null ? (
            <p className="text-slate-700">{payload.summary}</p>
          ) : (
            <p className="text-slate-500">No summary recorded.</p>
          )}
          {payload.identified_gaps.length > 0 ? (
            <div>
              <p className="font-medium text-slate-700">Identified gaps</p>
              <ul className="mt-1 list-disc space-y-1 pl-5 text-slate-600">
                {payload.identified_gaps.map((gap) => (
                  <li key={gap}>{gap}</li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="text-slate-500">No evidentiary gaps identified.</p>
          )}
        </div>
      ) : null}
    </ArtifactPanel>
  );
}
