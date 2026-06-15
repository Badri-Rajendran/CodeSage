import { useEffect, useState } from "react";
import { ArrowDownRight, ArrowUpRight, FlaskConical, Play, RefreshCw, Scale } from "lucide-react";
import { api, ApiError } from "../lib/api";
import type { EvalResult } from "../lib/types";
import { scoreTone, timeAgo } from "../lib/format";
import { Card, CardHeader, EmptyState, PageHeader, Spinner } from "../components/ui";

const DEFAULT_DATASET = "eval/datasets/sample.jsonl";

export function EvalPage() {
  const [runs, setRuns] = useState<EvalResult[] | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  // Run form
  const [model, setModel] = useState("");
  // Compare form
  const [baseline, setBaseline] = useState("claude-opus-4-7");
  const [candidate, setCandidate] = useState("claude-opus-4-8");

  async function load() {
    setRefreshing(true);
    try {
      setRuns(await api.evalRuns());
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setRefreshing(false);
    }
  }

  useEffect(() => {
    load();
    const t = setInterval(load, 6000); // results land asynchronously
    return () => clearInterval(t);
  }, []);

  async function launchRun() {
    try {
      await api.launchEvalRun({ dataset: DEFAULT_DATASET, model: model || undefined });
      setNotice("Eval run scheduled — results will appear below shortly.");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function launchCompare() {
    try {
      await api.launchEvalCompare({
        dataset: DEFAULT_DATASET,
        baseline,
        candidate,
        tolerance: 0.05,
      });
      setNotice(`Comparison scheduled: ${baseline} vs ${candidate}.`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  return (
    <div>
      <PageHeader
        title="Eval & Regression"
        subtitle="LLM-as-Judge scoring and cross-version regression detection."
        actions={
          <button onClick={load} className="btn-ghost" disabled={refreshing}>
            <RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} /> Refresh
          </button>
        }
      />

      <div className="mb-6 grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader
            title="Score a dataset"
            subtitle={DEFAULT_DATASET}
            icon={<FlaskConical className="h-4 w-4" />}
          />
          <div className="space-y-3 p-5">
            <input
              className="input font-mono"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="model (blank = configured default)"
            />
            <button onClick={launchRun} className="btn-primary w-full">
              <Play className="h-4 w-4" /> Run eval
            </button>
          </div>
        </Card>

        <Card>
          <CardHeader
            title="Compare two models"
            subtitle="Flag per-case regressions"
            icon={<Scale className="h-4 w-4" />}
          />
          <div className="space-y-3 p-5">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label">Baseline</label>
                <input
                  className="input font-mono"
                  value={baseline}
                  onChange={(e) => setBaseline(e.target.value)}
                />
              </div>
              <div>
                <label className="label">Candidate</label>
                <input
                  className="input font-mono"
                  value={candidate}
                  onChange={(e) => setCandidate(e.target.value)}
                />
              </div>
            </div>
            <button onClick={launchCompare} className="btn-primary w-full">
              <Scale className="h-4 w-4" /> Run comparison
            </button>
          </div>
        </Card>
      </div>

      {notice && (
        <Card className="mb-4 border-brand-500/30 bg-brand-500/5 p-3 text-sm text-brand-300">
          {notice}
        </Card>
      )}
      {error && (
        <Card className="mb-4 border-rose-500/30 bg-rose-500/5 p-3 text-sm text-rose-400">
          {error}
        </Card>
      )}

      {runs === null ? (
        <div className="flex items-center justify-center gap-2 py-16 text-sm text-ink-faint">
          <Spinner /> Loading results…
        </div>
      ) : runs.length === 0 ? (
        <Card>
          <EmptyState icon={<FlaskConical className="h-6 w-6" />} title="No eval results yet">
            Launch a run or comparison above. Results are saved under eval/results and listed
            here.
          </EmptyState>
        </Card>
      ) : (
        <div className="space-y-4">
          {runs.map((r) =>
            r.kind === "compare" ? (
              <CompareCard key={r.name} result={r} />
            ) : r.kind === "run" ? (
              <RunCard key={r.name} result={r} />
            ) : null,
          )}
        </div>
      )}
    </div>
  );
}

function RunCard({ result }: { result: EvalResult }) {
  const d = result.data;
  return (
    <Card>
      <CardHeader
        title={
          <span className="font-mono">{d.model}</span>
        }
        subtitle={`${d.n_cases} cases · ${timeAgo(result.modified)}`}
        actions={
          <div className="text-right">
            <div className={`text-2xl font-semibold ${scoreTone(d.mean_score)}`}>
              {d.mean_score?.toFixed(3)}
            </div>
            <div className="text-[10px] uppercase tracking-wide text-ink-faint">mean</div>
          </div>
        }
      />
      <div className="flex flex-wrap gap-2 p-5">
        {(d.per_case ?? []).map((c: any) => (
          <span
            key={c.case_id}
            className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 py-1 text-xs"
          >
            <span className="text-ink-faint">{c.case_id}</span>
            <span className={`font-medium tabular-nums ${scoreTone(c.score)}`}>
              {c.score.toFixed(2)}
            </span>
          </span>
        ))}
      </div>
    </Card>
  );
}

function CompareCard({ result }: { result: EvalResult }) {
  const d = result.data;
  const up = d.mean_delta >= 0;
  return (
    <Card className={d.has_regression ? "border-rose-500/40" : "border-emerald-500/30"}>
      <CardHeader
        title={
          <span className="font-mono text-sm">
            {d.baseline_model} → {d.candidate_model}
          </span>
        }
        subtitle={`${d.n_cases} cases · tolerance ${d.tolerance} · ${timeAgo(result.modified)}`}
        actions={
          <span
            className={`inline-flex items-center gap-1 rounded-lg px-2.5 py-1 text-sm font-semibold ${
              up ? "text-emerald-500" : "text-rose-400"
            }`}
          >
            {up ? <ArrowUpRight className="h-4 w-4" /> : <ArrowDownRight className="h-4 w-4" />}
            {up ? "+" : ""}
            {d.mean_delta?.toFixed(3)}
          </span>
        }
      />
      <div className="grid grid-cols-3 divide-x divide-line border-b border-line text-center">
        <Metric label="Baseline" value={d.baseline_mean?.toFixed(3)} />
        <Metric label="Candidate" value={d.candidate_mean?.toFixed(3)} />
        <Metric
          label="Regressions"
          value={d.n_regressions}
          tone={d.n_regressions > 0 ? "text-rose-400" : "text-emerald-500"}
        />
      </div>
      <div className="p-5">
        {d.has_regression ? (
          <div className="space-y-2">
            <p className="text-xs font-semibold uppercase tracking-wide text-rose-400">
              Regressed cases
            </p>
            {d.regressions.map((c: any) => (
              <div
                key={c.case_id}
                className="flex items-center justify-between rounded-lg border border-rose-500/20 bg-rose-500/5 px-3 py-2 text-sm"
              >
                <span className="font-mono text-ink-muted">{c.case_id}</span>
                <span className="tabular-nums text-ink-muted">
                  {c.baseline} → {c.candidate}{" "}
                  <span className="font-medium text-rose-400">({c.delta})</span>
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-emerald-500">
            ✓ No significant regressions within tolerance.
          </p>
        )}
      </div>
    </Card>
  );
}

function Metric({
  label,
  value,
  tone = "text-ink",
}: {
  label: string;
  value: string | number;
  tone?: string;
}) {
  return (
    <div className="px-4 py-4">
      <div className={`text-xl font-semibold tabular-nums ${tone}`}>{value}</div>
      <div className="mt-0.5 text-[10px] uppercase tracking-wide text-ink-faint">{label}</div>
    </div>
  );
}
