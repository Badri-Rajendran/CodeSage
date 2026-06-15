import { useState } from "react";
import { ChevronDown, FileCode2, Lightbulb } from "lucide-react";
import type { Finding } from "../lib/types";
import { reviewerAccent, severityClasses } from "../lib/format";
import { Badge } from "./ui";

export function SeverityBadge({ severity }: { severity: string }) {
  const c = severityClasses(severity);
  return (
    <Badge className={`${c.bg} ${c.border} ${c.text} uppercase`}>
      <span className={`h-1.5 w-1.5 rounded-full ${c.dot}`} />
      {severity}
    </Badge>
  );
}

export function FindingCard({ finding }: { finding: Finding }) {
  const [open, setOpen] = useState(false);
  const c = severityClasses(finding.severity);
  return (
    <div className={`overflow-hidden rounded-xl border ${c.border} bg-surface-2`}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-start gap-3 px-4 py-3 text-left"
      >
        <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${c.dot}`} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <SeverityBadge severity={finding.severity} />
            {finding.reviewer && (
              <span className={`text-xs font-medium ${reviewerAccent(finding.reviewer)}`}>
                {finding.reviewer}
              </span>
            )}
            <span className="text-xs text-ink-faint">
              {Math.round((finding.confidence ?? 0) * 100)}% confidence
            </span>
          </div>
          <p className="mt-1.5 text-sm font-medium text-ink">{finding.title}</p>
          {finding.file && (
            <p className="mt-1 flex items-center gap-1 font-mono text-xs text-ink-faint">
              <FileCode2 className="h-3 w-3" />
              {finding.file}
              {finding.line != null && `:${finding.line}`}
            </p>
          )}
        </div>
        <ChevronDown
          className={`mt-1 h-4 w-4 shrink-0 text-ink-faint transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && (
        <div className="animate-fade-in space-y-3 border-t border-line px-4 py-3 text-sm">
          <div>
            <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-faint">
              Rationale
            </p>
            <p className="leading-relaxed text-ink-muted">{finding.rationale}</p>
          </div>
          {finding.suggestion && (
            <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-3">
              <p className="mb-1 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-emerald-500">
                <Lightbulb className="h-3.5 w-3.5" /> Suggested fix
              </p>
              <p className="leading-relaxed text-ink-muted">{finding.suggestion}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
