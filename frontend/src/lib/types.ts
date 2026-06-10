/** Types mirroring backend response models (snake_case, as serialized by FastAPI). */

export type Role =
  | "claimant"
  | "imaging_specialist"
  | "medical_specialist"
  | "insurance_agent";

export type ArtifactStatus = "pending" | "running" | "complete" | "failed";

export type AuthenticityVerdict = "authentic" | "suspicious" | "likely_fraudulent";

export type Modality = "xray" | "ct" | "mri";

/** Mirrors `ClaimState` in backend/app/models/enums.py (8 states). */
export type ClaimState =
  | "SUBMITTED"
  | "IMAGING_REVIEW"
  | "RETURNED_TO_CLAIMANT"
  | "SPECIALIST_REVIEW"
  | "PENDING_FURTHER_TESTING"
  | "ADJUDICATION"
  | "APPROVED"
  | "REJECTED";

/** Mirrors `ClaimAction` in backend/app/models/enums.py. */
export type ClaimAction =
  | "submit"
  | "imaging_complete"
  | "forward"
  | "return_to_claimant"
  | "resubmit"
  | "send_to_insurer"
  | "request_further_testing"
  | "approve"
  | "reject";

/** Mirrors `DocumentKind` in backend/app/models/enums.py. */
export type DocumentKind = "imaging" | "medical_record" | "other";

/** Mirrors `UserOut` in backend/app/routers/auth.py. */
export interface UserOut {
  id: number;
  email: string;
  role: Role;
  full_name: string;
}

/** Mirrors `ClaimOut` in backend/app/routers/claims.py. */
export interface ClaimOut {
  id: number;
  claim_ref: string;
  claim_type: string;
  description: string;
  procedure_code: string;
  diagnosis_code: string;
  incident_date: string | null;
  amount_claimed: number;
  state: ClaimState;
  created_at: string;
  updated_at: string;
}

/** Mirrors `DocumentSummary` in backend/app/routers/claims.py (no size_bytes here). */
export interface DocumentSummary {
  id: number;
  filename: string;
  kind: DocumentKind;
  modality: Modality | null;
  has_preview: boolean;
}

/**
 * Mirrors `ReportOut` in backend/app/routers/claims.py.
 *
 * NOTE: the owner-scoped API returns authenticity/fraud fields, but the
 * claimant UI must never render them (see the privacy rule in
 * src/app/claimant/claims/[id]/page.tsx).
 */
export interface ReportOut {
  id: number;
  status: ArtifactStatus;
  modality: Modality | null;
  authenticity_verdict: AuthenticityVerdict | null;
  authenticity_risk: number | null;
  requires_mandatory_review: boolean;
  payload: Record<string, unknown> | null;
  error: string | null;
}

/** Mirrors `ClaimDetail` in backend/app/routers/claims.py. */
export interface ClaimDetail extends ClaimOut {
  documents: DocumentSummary[];
  diagnostic_report: ReportOut | null;
}

/** Mirrors `TimelineEntry` in backend/app/routers/claims.py. */
export interface TimelineEntry {
  action: ClaimAction;
  from_state: ClaimState | null;
  to_state: ClaimState;
  actor_role: string;
  note: string | null;
  created_at: string;
}

/** Mirrors `DocumentOut` in backend/app/routers/documents.py. */
export interface DocumentOut {
  id: number;
  filename: string;
  kind: DocumentKind;
  modality: Modality | null;
  size_bytes: number;
  sha256: string;
  has_preview: boolean;
}

/** Mirrors `AnalyzeOut` in backend/app/routers/claims.py. */
export interface AnalyzeOut {
  report_id: number;
  status: ArtifactStatus;
}

/** Mirrors `ResubmitOut` in backend/app/routers/claims.py. */
export interface ResubmitOut {
  state: ClaimState;
  report_id: number | null;
  report_status: ArtifactStatus | null;
}

/** Mirrors a row of GET /api/specialist/queue (claim plus its diagnostic-report artifact). */
export interface QueueItem {
  claim_id: number;
  claim_ref: string;
  claim_type: string;
  state: string;
  submitted_at: string;
  report_status: ArtifactStatus | null;
  modality: Modality | null;
  authenticity_verdict: AuthenticityVerdict | null;
  authenticity_risk: number | null;
  requires_mandatory_review: boolean;
}
