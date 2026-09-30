import { useState } from "react";
import { AlertTriangle, Check, X } from "lucide-react";
import { Card, Spinner } from "./ui";

/**
 * The approval gate: why a review paused, an optional note, and the decision.
 * Approving posts the review to its pull request (PR-mode reviews); rejecting
 * posts nothing. Used by the console, Review Detail and the Approval Queue.
 */
export function DecisionPanel({
  reasons,
  busy,
  canPost,
  onDecide,
  compact = false,
}: {
  reasons: string[];
  busy: boolean;
  canPost: boolean;
  onDecide: (approved: boolean, note: string) => void;
  compact?: boolean;
}) {
  const [note, setNote] = useState("");
  const body = (
    <div className="space-y-3">
      {!compact && (
        <p className="flex items-center gap-2 text-sm font-medium text-amber-500">
          <AlertTriangle className="h-4 w-4" />
          Paused at the approval gate
        </p>
      )}
      {reasons.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-5 text-sm text-ink-muted">
          {reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}
      <input
        className="input w-full"
        placeholder="Note (optional)"
        value={note}
        maxLength={2000}
        onChange={(e) => setNote(e.target.value)}
        disabled={busy}
      />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-ink-faint">
          {canPost
            ? "Approve posts this review to the pull request."
            : "Diff review: nothing is posted either way."}
        </p>
        <div className="flex gap-2">
          <button onClick={() => onDecide(false, note)} disabled={busy} className="btn-ghost">
            <X className="h-4 w-4" /> Reject
          </button>
          <button onClick={() => onDecide(true, note)} disabled={busy} className="btn-primary">
            {busy ? <Spinner /> : <Check className="h-4 w-4" />} Approve
          </button>
        </div>
      </div>
    </div>
  );
  return compact ? body : <Card className="border-amber-500/30 bg-amber-500/5 p-4">{body}</Card>;
}
