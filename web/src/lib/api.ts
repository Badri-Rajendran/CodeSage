// Typed REST client. All calls are relative to the same origin; in dev the Vite
// proxy forwards /api to the backend, and in production nginx does the same.

import { authHeaders, notifyAuthRequired } from "./auth";
import type {
  EvalResult,
  Review,
  ReviewJob,
  TelemetrySummary,
} from "./types";

const BASE = "/api/v1";

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...authHeaders() },
  });
  if (res.status === 401) notifyAuthRequired();
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

export interface ReviewInput {
  repo: string;
  pr_number?: number | null;
  diff?: string | null;
}

export interface ServiceInfo {
  name: string;
  version: string;
  llm_mode: string;
  model: string;
  hitl_threshold?: number;
  auth_required?: boolean;
}

export const api = {
  meta: () => http<ServiceInfo>("/meta"),

  startReview: (input: ReviewInput) =>
    http<ReviewJob>("/reviews/async", {
      method: "POST",
      body: JSON.stringify(input),
    }),

  runReviewSync: (input: ReviewInput) =>
    http<Review>("/reviews", { method: "POST", body: JSON.stringify(input) }),

  listReviews: (limit = 50) => http<Review[]>(`/reviews?limit=${limit}`),

  getReview: (id: string) => http<Review>(`/reviews/${id}`),

  approve: (id: string, approved: boolean) =>
    http<Review>(`/reviews/${id}/approve`, {
      method: "POST",
      body: JSON.stringify({ approved }),
    }),

  telemetry: () => http<TelemetrySummary>("/telemetry"),

  ingest: (input: { repo: string; path: string; replace: boolean }) =>
    http<{ repo: string; files: number; chunks: number }>("/ingest", {
      method: "POST",
      body: JSON.stringify(input),
    }),

  evalRuns: () => http<EvalResult[]>("/eval/runs"),

  launchEvalRun: (input: { dataset: string; model?: string }) =>
    http<{ status: string }>("/eval/runs", {
      method: "POST",
      body: JSON.stringify(input),
    }),

  launchEvalCompare: (input: {
    dataset: string;
    baseline: string;
    candidate: string;
    tolerance: number;
  }) =>
    http<{ status: string }>("/eval/compare", {
      method: "POST",
      body: JSON.stringify(input),
    }),
};

export const streamUrl = (id: string) => `${BASE}/reviews/${id}/stream`;
