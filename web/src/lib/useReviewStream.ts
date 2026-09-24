import { useCallback, useEffect, useRef, useState } from "react";
import { streamUrl } from "./api";
import { authHeaders, notifyAuthRequired } from "./auth";
import type {
  Finding,
  Review,
  ReviewEvent,
  Telemetry,
  Trace,
} from "./types";

export type StageStatus = "pending" | "running" | "completed" | "skipped";

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

// Mirrors STAGES in app/agents/graph.py (and STAGES in ./format.ts).
const STAGE_DEPS: Record<string, string[]> = {
  security: [],
  correctness: [],
  style: [],
  reflection: ["security", "correctness", "style"],
  judge: ["reflection"],
  revise: ["judge"],
  human_gate: ["judge"],
  publish: ["human_gate"],
};

// Conditional stages: never shown as running ahead of time (the stream only
// reports completions). Once the stage that follows them completes without
// them having run, they were skipped.
const OPTIONAL_STAGES: Record<string, string> = { revise: "human_gate" };

const STAGE_LABEL: Record<string, string> = {
  security: "Security reviewer",
  correctness: "Correctness reviewer",
  style: "Style reviewer",
  reflection: "Reflection",
  judge: "LLM-as-Judge",
  revise: "Revision round",
  human_gate: "Human gate",
  publish: "Publish",
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
    const after = OPTIONAL_STAGES[id];
    if (after) {
      const passed = next[after]?.status === "completed";
      next[id] = { ...(next[id] ?? {}), status: passed ? "skipped" : "pending" };
      continue;
    }
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
  const abortRef = useRef<AbortController | null>(null);

  const close = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  const reset = useCallback(() => {
    close();
    setLive(initialState());
  }, [close]);

  const start = useCallback(
    (reviewId: string) => {
      close();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      const fresh = () => setLive({ ...initialState(), reviewId, status: "streaming" });
      fresh();

      void consumeStream(reviewId, ctrl.signal, {
        // The broker replays every buffered event on (re)connect, so each
        // attempt rebuilds the view from a clean slate.
        onConnect: fresh,
        onEvent: (ev) => setLive((prev) => reduce(prev, ev)),
        onFatal: (error) =>
          setLive((prev) => ({
            ...prev,
            status: "failed",
            error,
            log: [{ ts: Date.now(), label: error, tone: "err" }, ...prev.log],
          })),
      });
    },
    [close],
  );

  useEffect(() => () => close(), [close]);

  return { live, start, reset };
}

const TERMINAL = new Set(["review.completed", "review.failed"]);
const MAX_ATTEMPTS = 5;
const RETRY_MS = 3000;

/**
 * Reads the SSE stream with fetch (not EventSource) so the API key can travel
 * in a header instead of the URL. Reconnects on dropped connections until a
 * terminal event arrives or the caller aborts.
 */
async function consumeStream(
  reviewId: string,
  signal: AbortSignal,
  cb: {
    onConnect: () => void;
    onEvent: (ev: ReviewEvent) => void;
    onFatal: (error: string) => void;
  },
): Promise<void> {
  for (let attempt = 1; attempt <= MAX_ATTEMPTS && !signal.aborted; attempt++) {
    try {
      const res = await fetch(streamUrl(reviewId), {
        headers: { Accept: "text/event-stream", ...authHeaders() },
        signal,
      });
      if (res.status === 401) {
        notifyAuthRequired();
        cb.onFatal("Unauthorized: set a valid API key to watch this review.");
        return;
      }
      if (!res.ok || !res.body) {
        cb.onFatal(`Stream failed: HTTP ${res.status}`);
        return;
      }
      if (attempt > 1) cb.onConnect();
      if (await readEvents(res.body, cb.onEvent)) return;
    } catch {
      if (signal.aborted) return;
    }
    await new Promise((r) => setTimeout(r, RETRY_MS));
  }
  if (!signal.aborted) cb.onFatal("Lost connection to the review stream.");
}

/** Parses SSE frames from `body`; resolves true once a terminal event is seen. */
async function readEvents(
  body: ReadableStream<Uint8Array>,
  onEvent: (ev: ReviewEvent) => void,
): Promise<boolean> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return false;
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const data = frame
        .split("\n")
        .filter((l) => l.startsWith("data:"))
        .map((l) => l.slice(5).trimStart())
        .join("\n");
      if (!data) continue; // retry hint or keep-alive comment
      let ev: ReviewEvent;
      try {
        ev = JSON.parse(data);
      } catch {
        continue; // ignore malformed frame
      }
      onEvent(ev);
      if (TERMINAL.has(ev.type)) {
        await reader.cancel();
        return true;
      }
    }
  }
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
      const label =
        ev.stage === "judge"
          ? `${STAGE_LABEL[ev.stage]} scored ${ev.judge_score?.toFixed(2) ?? "—"}`
          : ev.stage === "reflection" || ev.stage === "revise"
            ? `${STAGE_LABEL[ev.stage]} → ${ev.findings_count ?? 0} findings`
            : ev.stage === "human_gate"
              ? ev.requires_human_approval
                ? `Needs attention: ${(ev.gate_reasons ?? []).join("; ") || "gate tripped"}`
                : "Passed the gate"
              : ev.stage === "publish"
                ? ev.github_review_url
                  ? "Posted to the pull request"
                  : "Nothing to publish"
                : `${STAGE_LABEL[ev.stage]} done · ${ev.findings_count ?? 0} draft finding(s)`;
      return {
        ...prev,
        stages: recomputeRunning(stages),
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
