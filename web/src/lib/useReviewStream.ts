import { useCallback, useEffect, useRef, useState } from "react";
import { streamUrl } from "./api";
import type {
  Finding,
  Review,
  ReviewEvent,
  Telemetry,
  Trace,
} from "./types";

export type StageStatus = "pending" | "running" | "completed";

export interface StageState {
  status: StageStatus;
  startedAt?: number;
  completedAt?: number;
  findingsCount?: number;
  trace?: Trace | null;
}

export interface LiveReview {
  reviewId: string;
  model?: string;
  stages: Record<string, StageState>;
  draftFindings: Finding[];
  summary?: string;
  judgeScore?: number;
  judgeDimensions?: Record<string, number>;
  judgeRationale?: string;
  requiresHumanApproval?: boolean;
  telemetry?: Telemetry;
  result?: Review;
  status: "idle" | "streaming" | "completed" | "failed";
  error?: string;
  log: { ts: number; label: string; tone: "info" | "ok" | "warn" | "err" }[];
}

const STAGE_DEPS: Record<string, string[]> = {
  security: [],
  correctness: [],
  style: [],
  reflection: ["security", "correctness", "style"],
  judge: ["reflection"],
  human_gate: ["judge"],
};

const STAGE_LABEL: Record<string, string> = {
  security: "Security reviewer",
  correctness: "Correctness reviewer",
  style: "Style reviewer",
  reflection: "Reflection",
  judge: "LLM-as-Judge",
  human_gate: "Human gate",
};

function initialState(): LiveReview {
  return {
    reviewId: "",
    stages: {},
    draftFindings: [],
    status: "idle",
    log: [],
  };
}

/** Derive running/pending from completed set: a stage runs once all its deps are done. */
function recomputeRunning(stages: Record<string, StageState>): Record<string, StageState> {
  const next = { ...stages };
  for (const [id, deps] of Object.entries(STAGE_DEPS)) {
    const cur = next[id]?.status;
    if (cur === "completed") continue;
    const depsDone = deps.every((d) => next[d]?.status === "completed");
    next[id] = {
      ...(next[id] ?? { status: "pending" }),
      status: depsDone ? "running" : "pending",
      startedAt: depsDone ? next[id]?.startedAt ?? Date.now() : next[id]?.startedAt,
    };
  }
  return next;
}

/**
 * Subscribes to a review's SSE stream and reduces events into a live view model.
 * Returns the current model plus a `start(id)` to (re)connect.
 */
export function useReviewStream() {
  const [live, setLive] = useState<LiveReview>(initialState);
  const esRef = useRef<EventSource | null>(null);

  const close = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
  }, []);

  const reset = useCallback(() => {
    close();
    setLive(initialState());
  }, [close]);

  const start = useCallback(
    (reviewId: string) => {
      close();
      setLive({ ...initialState(), reviewId, status: "streaming" });

      const es = new EventSource(streamUrl(reviewId));
      esRef.current = es;

      const handle = (ev: ReviewEvent) => {
        setLive((prev) => reduce(prev, ev));
      };

      const types = [
        "review.started",
        "stage.completed",
        "telemetry.update",
        "review.completed",
        "review.failed",
      ];
      for (const t of types) {
        es.addEventListener(t, (e) => {
          try {
            handle(JSON.parse((e as MessageEvent).data));
          } catch {
            /* ignore malformed frame */
          }
        });
      }

      es.onerror = () => {
        // The browser auto-reconnects; if the stream already finished, close it.
        setLive((prev) => {
          if (prev.status === "completed" || prev.status === "failed") {
            es.close();
          }
          return prev;
        });
      };
    },
    [close],
  );

  useEffect(() => () => close(), [close]);

  return { live, start, reset };
}

function reduce(prev: LiveReview, ev: ReviewEvent): LiveReview {
  const log = prev.log;
  switch (ev.type) {
    case "review.started": {
      const stages: Record<string, StageState> = {};
      for (const id of ev.stages) stages[id] = { status: "pending" };
      return {
        ...prev,
        reviewId: ev.review_id,
        model: ev.model,
        status: "streaming",
        stages: recomputeRunning(stages),
        log: [
          { ts: Date.now(), label: `Pipeline started · ${ev.model}`, tone: "info" },
        ],
      };
    }
    case "stage.completed": {
      const stages = { ...prev.stages };
      stages[ev.stage] = {
        ...(stages[ev.stage] ?? { status: "running" }),
        status: "completed",
        completedAt: Date.now(),
        findingsCount: ev.findings_count,
        trace: ev.trace ?? stages[ev.stage]?.trace,
      };
      const draftFindings =
        ev.trace && typeof ev.findings_count === "number"
          ? prev.draftFindings
          : prev.draftFindings;
      const label =
        ev.stage === "judge"
          ? `${STAGE_LABEL[ev.stage]} scored ${ev.judge_score?.toFixed(2) ?? "—"}`
          : ev.stage === "reflection"
            ? `${STAGE_LABEL[ev.stage]} → ${ev.findings_count ?? 0} findings`
            : ev.stage === "human_gate"
              ? ev.requires_human_approval
                ? "Human approval required"
                : "Auto-approved by gate"
              : `${STAGE_LABEL[ev.stage]} done · ${ev.findings_count ?? 0} draft finding(s)`;
      return {
        ...prev,
        stages: recomputeRunning(stages),
        draftFindings,
        summary: ev.summary ?? prev.summary,
        judgeScore: ev.judge_score ?? prev.judgeScore,
        judgeDimensions: ev.judge_dimensions ?? prev.judgeDimensions,
        judgeRationale: ev.judge_rationale ?? prev.judgeRationale,
        requiresHumanApproval:
          ev.requires_human_approval ?? prev.requiresHumanApproval,
        log: [
          {
            ts: Date.now(),
            label,
            tone: ev.stage === "human_gate" && ev.requires_human_approval ? "warn" : "ok",
          },
          ...log,
        ],
      };
    }
    case "telemetry.update":
      return { ...prev, telemetry: ev.telemetry };
    case "review.completed":
      return {
        ...prev,
        status: "completed",
        result: ev.review,
        summary: ev.review.summary ?? prev.summary,
        judgeScore: ev.review.judge_score ?? prev.judgeScore,
        requiresHumanApproval: ev.review.requires_human_approval,
        telemetry: ev.review.telemetry ?? prev.telemetry,
        log: [{ ts: Date.now(), label: "Review complete", tone: "ok" }, ...log],
      };
    case "review.failed":
      return {
        ...prev,
        status: "failed",
        error: ev.error,
        log: [{ ts: Date.now(), label: `Failed: ${ev.error}`, tone: "err" }, ...log],
      };
    default:
      return prev;
  }
}
