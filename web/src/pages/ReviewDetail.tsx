import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { AlertTriangle, ArrowLeft, Check, Coins, ExternalLink, Sparkles, Terminal } from "lucide-react";
import { api, ApiError } from "../lib/api";
import type { Finding, Review } from "../lib/types";
import { fmtTokens, fmtUsd, SEVERITY_ORDER, shortId, timeAgo } from "../lib/format";
import { Card, CardHeader, EmptyState, PageHeader, Spinner, Stat } from "../components/ui";
import { FindingCard } from "../components/FindingCard";
import { JudgePanel } from "../components/JudgePanel";
import { TraceView } from "../components/TraceView";
import { DecisionPanel } from "../components/DecisionPanel";
import { StatusChip } from "../components/ReviewRow";

export function ReviewDetail() {
  const { id = "" } = useParams();
  const [review, setReview] = useState<Review | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      setReview(await api.getReview(id));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  // While a review runs (or resumes after a decision), refresh until it settles.
  useEffect(() => {
    if (review?.status !== "running") return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [review?.status]);

  async function decide(ok: boolean, note: string) {
    setBusy(true);
    try {
      await api.decide(id, ok, note);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const sorted: Finding[] = useMemo(
    () =>
      [...(review?.findings ?? [])].sort(
        (a, b) =>
          SEVERITY_ORDER.indexOf(a.severity as never) -
          SEVERITY_ORDER.indexOf(b.severity as never),
      ),
    [review],
  );

  if (error) {
    return (
      <Card>
        <EmptyState title="Review not found">{error}</EmptyState>
      </Card>
    );
  }
  if (!review) {
    return (
      <div className="flex items-center justify-center gap-2 py-24 text-sm text-ink-faint">
        <Spinner /> Loading review…
      </div>
    );
  }

  const tokens =
    (review.telemetry?.total_input_tokens ?? 0) +
    (review.telemetry?.total_output_tokens ?? 0);

  return (
    <div>
      <Link to="/history" className="mb-4 inline-flex items-center gap-1.5 text-sm text-ink-faint hover:text-ink">
        <ArrowLeft className="h-4 w-4" /> Back to history
      </Link>
      <PageHeader
        title={`${review.repo}${review.pr_number != null ? ` #${review.pr_number}` : ""}`}
        subtitle={`${shortId(review.id)} · ${review.model ?? ""} · ${timeAgo(review.created_at)}`}
        actions={
          <div className="flex items-center gap-3">
            <StatusChip review={review} />
            {review.github_review_url && (
              <a
                href={review.github_review_url}
                target="_blank"
                rel="noreferrer"
                className="btn-ghost"
              >
                <ExternalLink className="h-4 w-4" /> On GitHub
              </a>
            )}
          </div>
        }
      />

      <div className="mb-6 space-y-3">
        {review.status === "awaiting_approval" && (
          <DecisionPanel
            reasons={review.gate_reasons ?? []}
            busy={busy}
            canPost={review.head_sha != null}
            onDecide={decide}
          />
        )}
        {review.status === "failed" && review.error && (
          <Card className="border-rose-500/30 bg-rose-500/5 p-4 text-sm text-rose-400">
            <p className="flex items-center gap-2 font-medium">
              <AlertTriangle className="h-4 w-4" /> Review failed
            </p>
            <p className="mt-1 break-words text-rose-300/80">{review.error}</p>
          </Card>
        )}
        {review.gate_tripped && review.status !== "awaiting_approval" && (
          <Card className="p-4 text-sm">
            <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-faint">
              Approval gate
            </p>
            <ul className="list-disc space-y-0.5 pl-5 text-ink-muted">
              {(review.gate_reasons ?? []).map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
            {review.decision && (
              <p className="mt-2 text-ink-muted">
                <span className="font-medium text-ink">
                  {review.decision === "approved" ? "Approved" : "Rejected"}
                </span>
                {review.decided_at && ` ${timeAgo(review.decided_at)}`}
                {review.decision_note && `: “${review.decision_note}”`}
              </p>
            )}
          </Card>
        )}
        {review.budget_limited && (
          <p className="text-xs text-amber-500">
            The review budget was reached, so some agents stopped early.
          </p>
        )}
      </div>

      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Findings" value={review.findings.length} />
        <Stat label="Judge" value={review.judge_score?.toFixed(2) ?? "—"} />
        <Stat
          label="Cost"
          value={fmtUsd(review.telemetry?.total_cost_usd)}
          icon={<Coins className="h-4 w-4" />}
          tone="text-emerald-500"
        />
        <Stat label="Tokens" value={fmtTokens(tokens)} />
      </div>

      <div className="space-y-6">
        <JudgePanel
          score={review.judge_score}
          dimensions={review.judge_dimensions}
          rationale={review.judge_rationale}
        />

        <Card>
          <CardHeader
            title="Findings"
            subtitle={`${review.findings.length} after reflection`}
            icon={<Sparkles className="h-4 w-4" />}
          />
          <div className="space-y-2.5 p-5">
            {sorted.length ? (
              sorted.map((f, i) => <FindingCard key={i} finding={f} />)
            ) : (
              <EmptyState icon={<Check className="h-6 w-6" />} title="No issues found" />
            )}
            {review.summary && (
              <div className="mt-3 rounded-lg border border-line bg-surface p-3 text-sm">
                <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-faint">
                  Reflection summary
                </p>
                <p className="leading-relaxed text-ink-muted">{review.summary}</p>
              </div>
            )}
          </div>
        </Card>

        {review.traces && review.traces.length > 0 && (
          <Card>
            <CardHeader
              title="Agent reasoning traces"
              subtitle="Tool calls and reasoning per agent"
              icon={<Terminal className="h-4 w-4" />}
            />
            <div className="p-5">
              <TraceView traces={review.traces} />
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}
