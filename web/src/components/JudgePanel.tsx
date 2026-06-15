import { Scale } from "lucide-react";
import { fmtScore, scoreTone } from "../lib/format";
import { Card, CardHeader, MeterBar } from "./ui";

const DIMENSION_LABELS: Record<string, string> = {
  correctness: "Correctness",
  groundedness: "Groundedness",
  actionability: "Actionability",
  signal_to_noise: "Signal / Noise",
};

export function JudgePanel({
  score,
  dimensions,
  rationale,
}: {
  score?: number | null;
  dimensions?: Record<string, number> | null;
  rationale?: string | null;
}) {
  const dims = dimensions ?? {};
  return (
    <Card>
      <CardHeader
        title="LLM-as-Judge"
        subtitle="Quality scoring of the consolidated review"
        icon={<Scale className="h-4 w-4" />}
        actions={
          <div className="text-right">
            <div className={`text-3xl font-semibold tabular-nums ${scoreTone(score)}`}>
              {fmtScore(score)}
            </div>
            <div className="text-[10px] uppercase tracking-wide text-ink-faint">overall</div>
          </div>
        }
      />
      <div className="space-y-4 p-5">
        {Object.keys(dims).length > 0 && (
          <div className="grid gap-3 sm:grid-cols-2">
            {Object.entries(dims).map(([k, v]) => (
              <div key={k}>
                <div className="mb-1 flex items-center justify-between text-xs">
                  <span className="text-ink-muted">{DIMENSION_LABELS[k] ?? k}</span>
                  <span className={`font-medium tabular-nums ${scoreTone(v)}`}>
                    {v.toFixed(2)}
                  </span>
                </div>
                <MeterBar
                  value={v}
                  className={
                    v >= 0.8 ? "bg-emerald-500" : v >= 0.6 ? "bg-amber-500" : "bg-rose-500"
                  }
                />
              </div>
            ))}
          </div>
        )}
        {rationale && (
          <div className="rounded-lg border border-line bg-surface p-3">
            <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-faint">
              Judge rationale
            </p>
            <p className="text-sm leading-relaxed text-ink-muted">{rationale}</p>
          </div>
        )}
      </div>
    </Card>
  );
}
