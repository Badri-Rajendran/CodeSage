import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Check, Coins, Sparkles, Terminal, X } from "lucide-react";
import { api, ApiError } from "../lib/api";
import type { Finding, Review } from "../lib/types";
import { fmtTokens, fmtUsd, SEVERITY_ORDER, shortId, timeAgo } from "../lib/format";
import { Card, CardHeader, EmptyState, PageHeader, Spinner, Stat } from "../components/ui";
import { FindingCard } from "../components/FindingCard";
import { JudgePanel } from "../components/JudgePanel";
import { TraceView } from "../components/TraceView";

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

  async function decide(ok: boolean) {
    setBusy(true);
    try {
      setReview(await api.approve(id, ok));
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
          review.requires_human_approval ? (
            <div className="flex gap-2">
              <button onClick={() => decide(false)} disabled={busy} className="btn-ghost">
                <X className="h-4 w-4" /> Reject
              </button>
              <button onClick={() => decide(true)} disabled={busy} className="btn-primary">
                {busy ? <Spinner /> : <Check className="h-4 w-4" />} Approve
              </button>
            </div>
          ) : review.approved != null ? (
            <span
              className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm font-medium ${
                review.approved
                  ? "border-emerald-500/30 bg-emerald-500/5 text-emerald-500"
                  : "border-rose-500/30 bg-rose-500/5 text-rose-400"
              }`}
            >
              {review.approved ? <Check className="h-4 w-4" /> : <X className="h-4 w-4" />}
              {review.approved ? "Approved" : "Rejected"}
            </span>
          ) : null
        }
      />

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
              subtitle="ReAct trajectories per reviewer"
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
