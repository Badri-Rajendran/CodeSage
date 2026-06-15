import type { Severity } from "./types";

export const STAGES: {
  id: string;
  label: string;
  blurb: string;
  // Stages that must complete before this one is considered "running".
  deps: string[];
}[] = [
  { id: "security", label: "Security", blurb: "Vulnerability scan", deps: [] },
  { id: "correctness", label: "Correctness", blurb: "Logic & edge cases", deps: [] },
  { id: "style", label: "Style", blurb: "Idioms & clarity", deps: [] },
  {
    id: "reflection",
    label: "Reflection",
    blurb: "Consolidate & de-dupe",
    deps: ["security", "correctness", "style"],
  },
  { id: "judge", label: "LLM Judge", blurb: "Score the review", deps: ["reflection"] },
  { id: "human_gate", label: "Human Gate", blurb: "HITL decision", deps: ["judge"] },
];

export const SEVERITY_ORDER: Severity[] = ["critical", "high", "medium", "low", "info"];

export function severityClasses(sev: string): {
  text: string;
  bg: string;
  border: string;
  dot: string;
} {
  switch (sev) {
    case "critical":
      return {
        text: "text-rose-600 dark:text-rose-300",
        bg: "bg-rose-500/10",
        border: "border-rose-500/30",
        dot: "bg-rose-500",
      };
    case "high":
      return {
        text: "text-orange-600 dark:text-orange-300",
        bg: "bg-orange-500/10",
        border: "border-orange-500/30",
        dot: "bg-orange-500",
      };
    case "medium":
      return {
        text: "text-amber-600 dark:text-amber-300",
        bg: "bg-amber-500/10",
        border: "border-amber-500/30",
        dot: "bg-amber-500",
      };
    case "low":
      return {
        text: "text-sky-600 dark:text-sky-300",
        bg: "bg-sky-500/10",
        border: "border-sky-500/30",
        dot: "bg-sky-500",
      };
    default:
      return {
        text: "text-slate-500 dark:text-slate-300",
        bg: "bg-slate-500/10",
        border: "border-slate-500/30",
        dot: "bg-slate-400",
      };
  }
}

export function reviewerAccent(reviewer?: string | null): string {
  switch (reviewer) {
    case "security":
      return "text-rose-500";
    case "correctness":
      return "text-sky-500";
    case "style":
      return "text-violet-500";
    default:
      return "text-ink-faint";
  }
}

export function fmtUsd(n?: number | null): string {
  if (n == null) return "—";
  if (n === 0) return "$0.00";
  if (n < 0.01) return `$${n.toFixed(5)}`;
  return `$${n.toFixed(n < 1 ? 4 : 2)}`;
}

export function fmtTokens(n?: number | null): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export function fmtScore(n?: number | null): string {
  return n == null ? "—" : n.toFixed(2);
}

export function scoreTone(n?: number | null): string {
  if (n == null) return "text-ink-faint";
  if (n >= 0.8) return "text-emerald-500";
  if (n >= 0.6) return "text-amber-500";
  return "text-rose-500";
}

export function timeAgo(iso?: string | null): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  const secs = Math.max(1, Math.round((Date.now() - then) / 1000));
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

export function shortId(id: string): string {
  return id.slice(0, 8);
}
