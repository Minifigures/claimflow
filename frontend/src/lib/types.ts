/** Types mirroring backend response models (snake_case, as serialized by FastAPI). */

export type Role =
  | "claimant"
  | "imaging_specialist"
  | "medical_specialist"
  | "insurance_agent";

export type ArtifactStatus = "pending" | "running" | "complete" | "failed";

export type AuthenticityVerdict = "authentic" | "suspicious" | "likely_fraudulent";

export type Modality = "xray" | "ct" | "mri";

/** Mirrors `UserOut` in backend/app/routers/auth.py. */
export interface UserOut {
  id: number;
  email: string;
  role: Role;
  full_name: string;
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
