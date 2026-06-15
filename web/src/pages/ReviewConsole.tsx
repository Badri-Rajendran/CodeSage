import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle,
  Check,
  CircleDot,
  Coins,
  Play,
  RotateCcw,
  Sparkles,
  Terminal,
  X,
} from "lucide-react";
import { api, ApiError } from "../lib/api";
import { useReviewStream } from "../lib/useReviewStream";
import { fmtUsd, fmtTokens, SEVERITY_ORDER } from "../lib/format";
import { Card, CardHeader, EmptyState, PageHeader, Spinner, Stat } from "../components/ui";
import { StageTimeline } from "../components/StageTimeline";
import { FindingCard } from "../components/FindingCard";
import { JudgePanel } from "../components/JudgePanel";
import { TraceView } from "../components/TraceView";
import type { Finding } from "../lib/types";

const SAMPLES: { label: string; repo: string; diff: string }[] = [
  {
    label: "Command injection",
    repo: "octocat/hello-world",
    diff: `diff --git a/app.py b/app.py
@@ -1 +1,2 @@
-print(1)
+import os
+os.system(input())`,
  },
  {
    label: "Missing null check",
    repo: "acme/widgets",
    diff: `diff --git a/cart.py b/cart.py
@@ -10,6 +10,9 @@ def total(items):
     subtotal = 0
     for item in items:
-        subtotal += item.price * item.qty
+        subtotal += item["price"] * item["qty"]
+    discount = lookup_discount(user)  # user may be None
+    return subtotal - discount.amount`,
  },
];

export function ReviewConsole() {
  const { live, start, reset } = useReviewStream();
  const [repo, setRepo] = useState(SAMPLES[0].repo);
  const [prNumber, setPrNumber] = useState("");
  const [diff, setDiff] = useState(SAMPLES[0].diff);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [approving, setApproving] = useState(false);
  const [approved, setApproved] = useState<boolean | null>(null);

  const busy = submitting || live.status === "streaming";

  async function run() {
    setError(null);
    setApproved(null);
    setSubmitting(true);
    try {
      const job = await api.startReview({
        repo: repo.trim(),
        pr_number: prNumber ? Number(prNumber) : null,
        diff: diff.trim() || null,
      });
      start(job.id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  async function decide(ok: boolean) {
    if (!live.result) return;
    setApproving(true);
    try {
      await api.approve(live.result.id, ok);
      setApproved(ok);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setApproving(false);
    }
  }

  const findings: Finding[] = live.result?.findings ?? [];
  const sortedFindings = useMemo(
    () =>
      [...findings].sort(
        (a, b) =>
          SEVERITY_ORDER.indexOf(a.severity as never) -
          SEVERITY_ORDER.indexOf(b.severity as never),
      ),
    [findings],
  );
  const traces = live.result?.traces ?? [];

  return (
    <div>
      <PageHeader
        title="Review Console"
        subtitle="Submit a diff and watch the multi-agent pipeline reason in real time."
        actions={
          live.status !== "idle" && (
            <button onClick={reset} className="btn-ghost">
              <RotateCcw className="h-4 w-4" /> New review
            </button>
          )
        }
      />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
        {/* Main column */}
        <div className="space-y-6">
          {live.status === "idle" ? (
            <SubmitForm
              {...{ repo, setRepo, prNumber, setPrNumber, diff, setDiff }}
              onRun={run}
              busy={busy}
              error={error}
            />
          ) : (
            <>
              {live.requiresHumanApproval && live.status === "completed" && (
                <ApprovalBanner
                  approving={approving}
                  approved={approved}
                  onDecide={decide}
                />
              )}
              {live.status === "failed" && (
                <Card className="border-rose-500/30 bg-rose-500/5 p-4 text-sm text-rose-400">
                  <p className="flex items-center gap-2 font-medium">
                    <AlertTriangle className="h-4 w-4" /> Review failed
                  </p>
                  <p className="mt-1 text-rose-300/80">{live.error}</p>
                </Card>
              )}

              {(live.judgeScore != null || live.status === "completed") && (
                <JudgePanel
                  score={live.judgeScore}
                  dimensions={live.judgeDimensions}
                  rationale={live.judgeRationale}
                />
              )}

              <Card>
                <CardHeader
                  title="Findings"
                  subtitle={
                    live.status === "completed"
                      ? `${findings.length} issue${findings.length === 1 ? "" : "s"} after reflection`
                      : "Consolidating after reviewers finish…"
                  }
                  icon={<Sparkles className="h-4 w-4" />}
                />
                <div className="space-y-2.5 p-5">
                  {live.status !== "completed" ? (
                    <FindingsSkeleton />
                  ) : sortedFindings.length ? (
                    sortedFindings.map((f, i) => <FindingCard key={i} finding={f} />)
                  ) : (
                    <EmptyState icon={<Check className="h-6 w-6" />} title="No issues found">
                      The reviewers and reflection agent surfaced nothing actionable.
                    </EmptyState>
                  )}
                  {live.summary && live.status === "completed" && (
                    <div className="mt-3 rounded-lg border border-line bg-surface p-3 text-sm">
                      <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-faint">
                        Reflection summary
                      </p>
                      <p className="leading-relaxed text-ink-muted">{live.summary}</p>
                    </div>
                  )}
                </div>
              </Card>

              {traces.length > 0 && (
                <Card>
                  <CardHeader
                    title="Agent reasoning traces"
                    subtitle="ReAct trajectories — thought → action → observation"
                    icon={<Terminal className="h-4 w-4" />}
                  />
                  <div className="p-5">
                    <TraceView traces={traces} />
                  </div>
                </Card>
              )}
            </>
          )}
        </div>

        {/* Side column: live pipeline + activity */}
        <div className="space-y-6 lg:sticky lg:top-20 lg:self-start">
          <Card>
            <CardHeader
              title="Pipeline"
              subtitle={live.model ? `Running on ${live.model}` : "6 stages"}
              icon={<CircleDot className="h-4 w-4" />}
              actions={
                live.status === "streaming" ? (
                  <span className="flex items-center gap-1.5 text-xs font-medium text-brand-400">
                    <Spinner className="h-3 w-3" /> Live
                  </span>
                ) : live.status === "completed" ? (
                  <span className="text-xs font-medium text-emerald-500">Done</span>
                ) : null
              }
            />
            <div className="p-5">
              {live.status === "idle" ? (
                <p className="text-sm text-ink-faint">
                  Submit a review to start the security, correctness, and style reviewers in
                  parallel.
                </p>
              ) : (
                <StageTimeline stages={live.stages} />
              )}
            </div>
          </Card>

          {live.telemetry && (
            <div className="grid grid-cols-2 gap-3">
              <Stat
                label="Cost"
                value={fmtUsd(live.telemetry.total_cost_usd)}
                icon={<Coins className="h-4 w-4" />}
                tone="text-emerald-500"
              />
              <Stat
                label="Tokens"
                value={fmtTokens(
                  live.telemetry.total_input_tokens + live.telemetry.total_output_tokens,
                )}
                hint={`${live.telemetry.calls} calls`}
              />
            </div>
          )}

          {live.status !== "idle" && (
            <Card>
              <CardHeader title="Activity" subtitle="Streamed events" />
              <ol className="max-h-72 space-y-2 overflow-auto p-4 text-sm">
                {live.log.map((l, i) => (
                  <li key={i} className="flex items-start gap-2 animate-fade-in">
                    <span
                      className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${
                        l.tone === "ok"
                          ? "bg-emerald-500"
                          : l.tone === "warn"
                            ? "bg-amber-500"
                            : l.tone === "err"
                              ? "bg-rose-500"
                              : "bg-brand-500"
                      }`}
                    />
                    <span className="text-ink-muted">{l.label}</span>
                  </li>
                ))}
              </ol>
            </Card>
          )}

          {live.result && (
            <Link
              to={`/reviews/${live.result.id}`}
              className="btn-ghost w-full justify-center"
            >
              Open full review →
            </Link>
          )}
        </div>
      </div>
    </div>
  );
}

function SubmitForm({
  repo,
  setRepo,
  prNumber,
  setPrNumber,
  diff,
  setDiff,
  onRun,
  busy,
  error,
}: {
  repo: string;
  setRepo: (v: string) => void;
  prNumber: string;
  setPrNumber: (v: string) => void;
  diff: string;
  setDiff: (v: string) => void;
  onRun: () => void;
  busy: boolean;
  error: string | null;
}) {
  return (
    <Card>
      <CardHeader
        title="New review"
        subtitle="Paste a unified diff, or give a PR number to fetch from GitHub."
        icon={<Play className="h-4 w-4" />}
      />
      <div className="space-y-4 p-5">
        <div className="grid gap-4 sm:grid-cols-[1fr_140px]">
          <div>
            <label className="label">Repository</label>
            <input
              className="input font-mono"
              value={repo}
              onChange={(e) => setRepo(e.target.value)}
              placeholder="owner/name"
            />
          </div>
          <div>
            <label className="label">PR # (optional)</label>
            <input
              className="input font-mono"
              value={prNumber}
              onChange={(e) => setPrNumber(e.target.value.replace(/\D/g, ""))}
              placeholder="42"
            />
          </div>
        </div>
        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <label className="label mb-0">Unified diff</label>
            <div className="flex gap-1.5">
              {SAMPLES.map((s) => (
                <button
                  key={s.label}
                  onClick={() => {
                    setRepo(s.repo);
                    setDiff(s.diff);
                  }}
                  className="rounded-md border border-line px-2 py-1 text-[11px] text-ink-muted hover:bg-surface-3 hover:text-ink"
                >
                  {s.label}
                </button>
              ))}
            </div>
          </div>
          <textarea
            className="input min-h-[220px] font-mono text-xs leading-relaxed"
            value={diff}
            onChange={(e) => setDiff(e.target.value)}
            spellCheck={false}
            placeholder="diff --git a/file b/file&#10;@@ ... @@"
          />
        </div>
        {error && (
          <p className="flex items-center gap-2 rounded-lg border border-rose-500/30 bg-rose-500/5 px-3 py-2 text-sm text-rose-400">
            <AlertTriangle className="h-4 w-4" /> {error}
          </p>
        )}
        <button onClick={onRun} disabled={busy || !repo.trim()} className="btn-primary w-full">
          {busy ? <Spinner /> : <Play className="h-4 w-4" />}
          {busy ? "Reviewing…" : "Run review"}
        </button>
      </div>
    </Card>
  );
}

function ApprovalBanner({
  approving,
  approved,
  onDecide,
}: {
  approving: boolean;
  approved: boolean | null;
  onDecide: (ok: boolean) => void;
}) {
  if (approved !== null) {
    return (
      <Card
        className={`p-4 ${
          approved
            ? "border-emerald-500/30 bg-emerald-500/5"
            : "border-rose-500/30 bg-rose-500/5"
        }`}
      >
        <p
          className={`flex items-center gap-2 text-sm font-medium ${
            approved ? "text-emerald-500" : "text-rose-400"
          }`}
        >
          {approved ? <Check className="h-4 w-4" /> : <X className="h-4 w-4" />}
          Review {approved ? "approved" : "rejected"}.
        </p>
      </Card>
    );
  }
  return (
    <Card className="border-amber-500/30 bg-amber-500/5 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="flex items-center gap-2 text-sm font-medium text-amber-500">
          <AlertTriangle className="h-4 w-4" />
          Human-in-the-loop gate tripped — this review needs sign-off.
        </p>
        <div className="flex gap-2">
          <button
            onClick={() => onDecide(false)}
            disabled={approving}
            className="btn-ghost"
          >
            <X className="h-4 w-4" /> Reject
          </button>
          <button
            onClick={() => onDecide(true)}
            disabled={approving}
            className="btn-primary"
          >
            {approving ? <Spinner /> : <Check className="h-4 w-4" />} Approve
          </button>
        </div>
      </div>
    </Card>
  );
}

function FindingsSkeleton() {
  return (
    <div className="space-y-2.5">
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="h-16 animate-pulse rounded-xl border border-line bg-surface-3/50"
        />
      ))}
    </div>
  );
}
