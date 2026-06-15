import { useState } from "react";
import { Brain, ChevronDown, Eye, Wrench } from "lucide-react";
import type { Trace, TraceStep } from "../lib/types";
import { reviewerAccent } from "../lib/format";

const STEP_META: Record<
  TraceStep["kind"],
  { icon: typeof Brain; label: string; color: string }
> = {
  thought: { icon: Brain, label: "Thought", color: "text-violet-400" },
  action: { icon: Wrench, label: "Action", color: "text-amber-400" },
  observation: { icon: Eye, label: "Observation", color: "text-sky-400" },
};

export function TraceView({ traces }: { traces: Trace[] }) {
  if (!traces.length) return null;
  return (
    <div className="space-y-2">
      {traces.map((t, i) => (
        <TraceAccordion key={`${t.agent}-${i}`} trace={t} defaultOpen={i === 0} />
      ))}
    </div>
  );
}

function TraceAccordion({ trace, defaultOpen }: { trace: Trace; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(!!defaultOpen);
  return (
    <div className="overflow-hidden rounded-lg border border-line bg-surface">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-4 py-2.5 text-left"
      >
        <span className="flex items-center gap-2 text-sm font-medium">
          <span className={`font-mono text-xs ${reviewerAccent(trace.agent)}`}>
            {trace.agent}
          </span>
          <span className="text-ink-faint">· {trace.steps.length} steps</span>
        </span>
        <ChevronDown
          className={`h-4 w-4 text-ink-faint transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && (
        <ol className="animate-fade-in space-y-3 border-t border-line px-4 py-3">
          {trace.steps.map((step, i) => {
            const meta = STEP_META[step.kind];
            const Icon = meta.icon;
            return (
              <li key={i} className="flex gap-3">
                <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${meta.color}`} />
                <div className="min-w-0 flex-1">
                  <p className="flex items-center gap-2">
                    <span className={`text-xs font-semibold uppercase ${meta.color}`}>
                      {meta.label}
                    </span>
                    {step.tool && (
                      <span className="rounded bg-surface-3 px-1.5 py-0.5 font-mono text-[10px] text-ink-muted">
                        {step.tool}
                      </span>
                    )}
                  </p>
                  <p className="mt-0.5 whitespace-pre-wrap text-sm leading-relaxed text-ink-muted">
                    {step.content}
                  </p>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
