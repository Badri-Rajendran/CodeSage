import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ShieldCheck } from "lucide-react";
import { api, ApiError } from "../lib/api";
import type { Finding, Review } from "../lib/types";
import { fmtScore, scoreTone, shortId, timeAgo } from "../lib/format";
import { Card, CardHeader, EmptyState, PageHeader, Spinner } from "../components/ui";
import { SeverityBadge } from "../components/FindingCard";
import { DecisionPanel } from "../components/DecisionPanel";

export function ApprovalQueue() {
  const [reviews, setReviews] = useState<Review[] | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      const all = await api.listReviews(200);
      setReviews(all.filter((r) => r.status === "awaiting_approval"));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => {
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, []);

  async function decide(id: string, ok: boolean, note: string) {
    setBusyId(id);
    try {
      await api.decide(id, ok, note);
      setReviews((prev) => (prev ?? []).filter((r) => r.id !== id));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div>
      <PageHeader
        title="Approval Queue"
        subtitle="Reviews paused at the approval gate. Approve posts the review to its PR; reject posts nothing."
      />
      {error && (
        <Card className="mb-4 border-rose-500/30 bg-rose-500/5 p-3 text-sm text-rose-400">
          {error}
        </Card>
      )}

      {reviews === null ? (
        <div className="flex items-center justify-center gap-2 py-16 text-sm text-ink-faint">
          <Spinner /> Loading queue…
        </div>
      ) : reviews.length === 0 ? (
        <Card>
          <EmptyState icon={<ShieldCheck className="h-6 w-6" />} title="Queue is clear">
            No reviews are currently waiting for human approval.
          </EmptyState>
        </Card>
      ) : (
        <div className="space-y-4">
          {reviews.map((r) => (
            <QueueCard
              key={r.id}
              review={r}
              busy={busyId === r.id}
              onDecide={(ok, note) => decide(r.id, ok, note)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function QueueCard({
  review,
  busy,
  onDecide,
}: {
  review: Review;
  busy: boolean;
  onDecide: (ok: boolean, note: string) => void;
}) {
  const top: Finding[] = [...review.findings]
    .sort((a, b) => (b.confidence ?? 0) - (a.confidence ?? 0))
    .slice(0, 3);
  return (
    <Card>
      <CardHeader
        title={
          <Link to={`/reviews/${review.id}`} className="font-mono hover:text-brand-400">
            {review.repo}
            {review.pr_number != null && ` #${review.pr_number}`}
          </Link>
        }
        subtitle={`${shortId(review.id)} · ${timeAgo(review.created_at)}`}
        actions={
          <div className="text-right">
            <div className={`text-xl font-semibold ${scoreTone(review.judge_score)}`}>
              {fmtScore(review.judge_score)}
            </div>
            <div className="text-[10px] uppercase tracking-wide text-ink-faint">judge</div>
          </div>
        }
      />
      <div className="space-y-3 p-5">
        {review.summary && (
          <p className="text-sm leading-relaxed text-ink-muted">{review.summary}</p>
        )}
        <div className="space-y-1.5">
          {top.map((f, i) => (
            <div key={i} className="flex items-center gap-2 text-sm">
              <SeverityBadge severity={f.severity} />
              <span className="truncate text-ink-muted">{f.title}</span>
            </div>
          ))}
        </div>
        <DecisionPanel
          compact
          reasons={review.gate_reasons ?? []}
          busy={busy}
          canPost={review.head_sha != null}
          onDecide={onDecide}
        />
      </div>
    </Card>
  );
}
