// Mirrors app/api/schemas.py and the SSE event payloads from the backend.

export type Severity = "critical" | "high" | "medium" | "low" | "info";

export interface Finding {
  id?: string | null;
  reviewer?: string | null;
  category?: string | null;
  title: string;
  severity: Severity | string;
  rationale: string;
  confidence: number;
  path?: string | null;
  line?: number | null;
  end_line?: number | null;
  suggestion?: string | null;
  evidence?: string[];
  merged_from?: string[];
}

export interface TraceStep {
  kind: "thought" | "action" | "observation";
  content: string;
  tool?: string;
}

export interface Trace {
  agent: string;
  steps: TraceStep[];
}

export interface UsageEvent {
  component: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  cost_usd: number;
}

export interface Telemetry {
  total_cost_usd: number;
  total_input_tokens: number;
  total_output_tokens: number;
  calls: number;
  by_component: UsageEvent[];
}

export type ReviewStatus =
  | "running"
  | "awaiting_approval"
  | "completed"
  | "rejected"
  | "failed";

export interface Review {
  id: string;
  repo: string;
  pr_number: number | null;
  status?: ReviewStatus | string;
  model?: string;
  findings: Finding[];
  summary?: string | null;
  judge_score?: number | null;
  judge_rationale?: string | null;
  judge_dimensions?: Record<string, number> | null;
  requires_human_approval: boolean;
  approved?: boolean | null;
  traces?: Trace[] | null;
  telemetry?: Telemetry | null;
  created_at?: string | null;
  pr_title?: string | null;
  head_sha?: string | null;
  gate_tripped?: boolean;
  gate_reasons?: string[];
  budget_limited?: boolean;
  revision_count?: number;
  decision?: "approved" | "rejected" | null;
  decision_note?: string | null;
  decided_at?: string | null;
  github_review_url?: string | null;
  error?: string | null;
}

export interface ReviewJob {
  id: string;
  repo: string;
  pr_number: number | null;
  status: string;
  stream_url: string;
}

export interface TelemetrySummary {
  total_cost_usd: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_calls: number;
  total_reviews: number;
  by_component: {
    component: string;
    cost_usd: number;
    input_tokens: number;
    output_tokens: number;
    calls: number;
  }[];
}

export interface EvalResult {
  name: string;
  kind: "run" | "compare" | "comparand";
  modified: string;
  data: any;
}

// ── SSE event shapes ──────────────────────────────────────────────────────────
export type StageName =
  | "security"
  | "correctness"
  | "style"
  | "reflection"
  | "judge"
  | "revise"
  | "human_gate"
  | "publish";

export interface ReviewStartedEvent {
  type: "review.started";
  review_id: string;
  repo: string;
  pr_number: number | null;
  model: string;
  stages: StageName[];
  pr_mode?: boolean;
}

export interface StageCompletedEvent {
  type: "stage.completed";
  review_id: string;
  stage: StageName;
  findings_count?: number;
  trace?: Trace | null;
  summary?: string;
  judge_score?: number;
  judge_dimensions?: Record<string, number>;
  judge_rationale?: string;
  requires_human_approval?: boolean;
  gate_reasons?: string[];
  github_review_url?: string | null;
}

export interface TelemetryUpdateEvent {
  type: "telemetry.update";
  review_id: string;
  telemetry: Telemetry;
}

export interface ReviewCompletedEvent {
  type: "review.completed";
  review_id: string;
  review: Review;
}

export interface ReviewAwaitingApprovalEvent {
  type: "review.awaiting_approval";
  review_id: string;
  gate_reasons: string[];
  judge_score: number | null;
  review: Review;
}

export interface ReviewFailedEvent {
  type: "review.failed";
  review_id: string;
  error: string;
}

export type ReviewEvent =
  | ReviewStartedEvent
  | StageCompletedEvent
  | TelemetryUpdateEvent
  | ReviewCompletedEvent
  | ReviewAwaitingApprovalEvent
  | ReviewFailedEvent;
