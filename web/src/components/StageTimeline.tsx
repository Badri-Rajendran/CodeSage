import {
  Bug,
  Check,
  GitMerge,
  Loader2,
  RefreshCw,
  Scale,
  Send,
  ShieldCheck,
  Sparkles,
  UserCheck,
} from "lucide-react";
import type { ReactNode } from "react";
import { STAGES } from "../lib/format";
import type { StageState } from "../lib/useReviewStream";

const ICONS: Record<string, ReactNode> = {
  security: <ShieldCheck className="h-4 w-4" />,
  correctness: <Bug className="h-4 w-4" />,
  style: <Sparkles className="h-4 w-4" />,
  reflection: <GitMerge className="h-4 w-4" />,
  judge: <Scale className="h-4 w-4" />,
  revise: <RefreshCw className="h-4 w-4" />,
  human_gate: <UserCheck className="h-4 w-4" />,
  publish: <Send className="h-4 w-4" />,
};

export function StageTimeline({
  stages,
}: {
  stages: Record<string, StageState>;
}) {
  return (
    <ol className="space-y-1">
      {STAGES.map((stage, i) => {
        const st = stages[stage.id]?.status ?? "pending";
        const count = stages[stage.id]?.findingsCount;
        const isLast = i === STAGES.length - 1;
        return (
          <li key={stage.id} className="relative flex gap-3 pb-1">
            {!isLast && (
              <span
                className={`absolute left-[15px] top-8 h-[calc(100%-1rem)] w-px ${
                  st === "completed" ? "bg-brand-500/50" : "bg-line"
                }`}
              />
            )}
            <StageNode status={st}>{ICONS[stage.id]}</StageNode>
            <div className="flex flex-1 items-center justify-between pt-1">
              <div>
                <p
                  className={`text-sm font-medium ${
                    st === "pending" || st === "skipped" ? "text-ink-faint" : "text-ink"
                  }`}
                >
                  {stage.label}
                </p>
                <p className="text-xs text-ink-faint">{stage.blurb}</p>
              </div>
              <StageStatusChip status={st} count={count} stageId={stage.id} />
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function StageNode({
  status,
  children,
}: {
  status: string;
  children: ReactNode;
}) {
  if (status === "completed") {
    return (
      <span className="z-10 grid h-8 w-8 place-items-center rounded-full bg-brand-600 text-white">
        <Check className="h-4 w-4" />
      </span>
    );
  }
  if (status === "awaiting") {
    return (
      <span className="z-10 grid h-8 w-8 place-items-center rounded-full border-2 border-amber-500 bg-surface text-amber-500">
        {children}
      </span>
    );
  }
  if (status === "running") {
    return (
      <span className="z-10 grid h-8 w-8 animate-pulse-ring place-items-center rounded-full border-2 border-brand-500 bg-surface text-brand-400">
        <Loader2 className="h-4 w-4 animate-spin" />
      </span>
    );
  }
  return (
    <span className="z-10 grid h-8 w-8 place-items-center rounded-full border border-line bg-surface text-ink-faint">
      {children}
    </span>
  );
}

function StageStatusChip({
  status,
  count,
  stageId,
}: {
  status: string;
  count?: number;
  stageId: string;
}) {
  if (status === "completed") {
    const showCount =
      typeof count === "number" &&
      ["security", "correctness", "style", "reflection", "revise"].includes(stageId);
    return (
      <span className="text-xs font-medium text-emerald-500">
        {showCount ? `${count} finding${count === 1 ? "" : "s"}` : "Done"}
      </span>
    );
  }
  if (status === "running") {
    return <span className="text-xs font-medium text-brand-400">Running…</span>;
  }
  if (status === "skipped") {
    return <span className="text-xs text-ink-faint">Not needed</span>;
  }
  if (status === "awaiting") {
    return <span className="text-xs font-medium text-amber-500">Waiting for you</span>;
  }
  return <span className="text-xs text-ink-faint">Queued</span>;
}
