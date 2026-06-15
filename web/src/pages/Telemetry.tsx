import { useEffect, useState } from "react";
import { Coins, Cpu, Layers, RefreshCw } from "lucide-react";
import { api } from "../lib/api";
import type { TelemetrySummary } from "../lib/types";
import { fmtTokens, fmtUsd, reviewerAccent } from "../lib/format";
import { Card, CardHeader, EmptyState, MeterBar, PageHeader, Spinner, Stat } from "../components/ui";

export function TelemetryPage() {
  const [data, setData] = useState<TelemetrySummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  async function load() {
    setRefreshing(true);
    try {
      setData(await api.telemetry());
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setRefreshing(false);
    }
  }

  useEffect(() => {
    load();
    const t = setInterval(load, 10_000);
    return () => clearInterval(t);
  }, []);

  const maxCost = Math.max(...(data?.by_component.map((c) => c.cost_usd) ?? [0]), 0.0000001);
  const tokens = (data?.total_input_tokens ?? 0) + (data?.total_output_tokens ?? 0);
  const avgCost =
    data && data.total_reviews ? data.total_cost_usd / data.total_reviews : 0;

  return (
    <div>
      <PageHeader
        title="Cost & Telemetry"
        subtitle="Token usage and USD cost metered across every Claude call."
        actions={
          <button onClick={load} className="btn-ghost" disabled={refreshing}>
            <RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} /> Refresh
          </button>
        }
      />

      {data === null ? (
        <div className="flex items-center justify-center gap-2 py-16 text-sm text-ink-faint">
          <Spinner /> Loading telemetry…
        </div>
      ) : error ? (
        <Card>
          <EmptyState title="Could not load telemetry">{error}</EmptyState>
        </Card>
      ) : (
        <>
          <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat
              label="Total spend"
              value={fmtUsd(data.total_cost_usd)}
              icon={<Coins className="h-4 w-4" />}
              tone="text-emerald-500"
              hint={`${data.total_reviews} reviews`}
            />
            <Stat
              label="Avg / review"
              value={fmtUsd(avgCost)}
              icon={<Layers className="h-4 w-4" />}
            />
            <Stat label="Total tokens" value={fmtTokens(tokens)} icon={<Cpu className="h-4 w-4" />} />
            <Stat label="LLM calls" value={data.total_calls} />
          </div>

          <Card>
            <CardHeader
              title="Cost by component"
              subtitle="Where the budget goes across the pipeline"
            />
            <div className="space-y-4 p-5">
              {data.by_component.length === 0 ? (
                <EmptyState title="No usage recorded yet">
                  Run a review to populate cost telemetry.
                </EmptyState>
              ) : (
                data.by_component.map((c) => (
                  <div key={c.component}>
                    <div className="mb-1.5 flex items-center justify-between text-sm">
                      <span className={`font-medium ${reviewerAccent(c.component)}`}>
                        {c.component}
                      </span>
                      <span className="flex items-center gap-3 text-xs text-ink-faint">
                        <span>{fmtTokens(c.input_tokens + c.output_tokens)} tok</span>
                        <span>{c.calls} calls</span>
                        <span className="w-16 text-right font-medium tabular-nums text-emerald-500">
                          {fmtUsd(c.cost_usd)}
                        </span>
                      </span>
                    </div>
                    <MeterBar value={c.cost_usd} max={maxCost} className="bg-brand-500" />
                  </div>
                ))
              )}
            </div>
          </Card>

          {data.by_component.length > 0 && (
            <Card className="mt-6">
              <CardHeader title="Token breakdown" subtitle="Input vs output per component" />
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-faint">
                      <th className="px-5 py-3 font-semibold">Component</th>
                      <th className="px-5 py-3 text-right font-semibold">Input</th>
                      <th className="px-5 py-3 text-right font-semibold">Output</th>
                      <th className="px-5 py-3 text-right font-semibold">Calls</th>
                      <th className="px-5 py-3 text-right font-semibold">Cost</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {data.by_component.map((c) => (
                      <tr key={c.component}>
                        <td className={`px-5 py-3 font-medium ${reviewerAccent(c.component)}`}>
                          {c.component}
                        </td>
                        <td className="px-5 py-3 text-right tabular-nums text-ink-muted">
                          {fmtTokens(c.input_tokens)}
                        </td>
                        <td className="px-5 py-3 text-right tabular-nums text-ink-muted">
                          {fmtTokens(c.output_tokens)}
                        </td>
                        <td className="px-5 py-3 text-right tabular-nums text-ink-muted">
                          {c.calls}
                        </td>
                        <td className="px-5 py-3 text-right font-medium tabular-nums text-emerald-500">
                          {fmtUsd(c.cost_usd)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
