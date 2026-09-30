import { Link } from "react-router-dom";
import { AlertTriangle, Check, CircleDashed, Clock, X } from "lucide-react";
import type { Review } from "../lib/types";
import { fmtScore, scoreTone, severityClasses, shortId, timeAgo } from "../lib/format";

const CHIP = "inline-flex items-center gap-1 text-xs font-medium";

export function StatusChip({ review }: { review: Review }) {
  switch (review.status) {
    case "running":
      return (
        <span className={`${CHIP} text-brand-400`}>
          <CircleDashed className="h-3.5 w-3.5 animate-spin" /> Running
        </span>
      );
    case "awaiting_approval":
      return (
        <span className={`${CHIP} text-amber-500`}>
          <AlertTriangle className="h-3.5 w-3.5" /> Awaiting approval
        </span>
      );
    case "failed":
      return (
        <span className={`${CHIP} text-rose-400`}>
          <X className="h-3.5 w-3.5" /> Failed
        </span>
      );
    case "rejected":
      return (
        <span className={`${CHIP} text-rose-400`}>
          <X className="h-3.5 w-3.5" /> Rejected
        </span>
      );
  }
  if (review.decision === "approved") {
    return (
      <span className={`${CHIP} text-emerald-500`}>
        <Check className="h-3.5 w-3.5" /> Approved{review.github_review_url ? " · posted" : ""}
      </span>
    );
  }
  return (
    <span className={`${CHIP} text-ink-faint`}>
      <Check className="h-3.5 w-3.5" /> Passed{review.github_review_url ? " · posted" : ""}
    </span>
  );
}

function SeverityDots({ review }: { review: Review }) {
  const counts = review.findings.reduce<Record<string, number>>((acc, f) => {
    acc[f.severity] = (acc[f.severity] ?? 0) + 1;
    return acc;
  }, {});
  const order = ["critical", "high", "medium", "low", "info"];
  const present = order.filter((s) => counts[s]);
  if (!present.length)
    return <span className="text-xs text-ink-faint">no findings</span>;
  return (
    <div className="flex items-center gap-1.5">
      {present.map((s) => (
        <span key={s} className="flex items-center gap-1 text-xs text-ink-muted">
          <span className={`h-2 w-2 rounded-full ${severityClasses(s).dot}`} />
          {counts[s]}
        </span>
      ))}
    </div>
  );
}

export function ReviewRow({ review }: { review: Review }) {
  return (
    <Link
      to={`/reviews/${review.id}`}
      className="flex items-center gap-4 px-5 py-3.5 transition-colors hover:bg-surface-3"
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate font-mono text-sm font-medium text-ink">
            {review.repo}
            {review.pr_number != null && (
              <span className="text-ink-faint"> #{review.pr_number}</span>
            )}
          </span>
          <span className="rounded bg-surface-3 px-1.5 py-0.5 font-mono text-[10px] text-ink-faint">
            {shortId(review.id)}
          </span>
        </div>
        <div className="mt-1 flex items-center gap-3">
          <SeverityDots review={review} />
          <span className="flex items-center gap-1 text-xs text-ink-faint">
            <Clock className="h-3 w-3" />
            {timeAgo(review.created_at)}
          </span>
        </div>
      </div>
      <div className="hidden text-right sm:block">
        <div className={`text-sm font-semibold tabular-nums ${scoreTone(review.judge_score)}`}>
          {fmtScore(review.judge_score)}
        </div>
        <div className="text-[10px] uppercase tracking-wide text-ink-faint">judge</div>
      </div>
      <StatusChip review={review} />
    </Link>
  );
}
