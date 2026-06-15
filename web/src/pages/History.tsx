import { useEffect, useState } from "react";
import { History as HistoryIcon, RefreshCw, Search } from "lucide-react";
import { api } from "../lib/api";
import type { Review } from "../lib/types";
import { Card, EmptyState, PageHeader, Spinner } from "../components/ui";
import { ReviewRow } from "../components/ReviewRow";

export function HistoryPage() {
  const [reviews, setReviews] = useState<Review[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  async function load() {
    setRefreshing(true);
    try {
      setReviews(await api.listReviews(100));
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setRefreshing(false);
    }
  }

  useEffect(() => {
    load();
    const t = setInterval(load, 8000); // surface in-flight + new reviews
    return () => clearInterval(t);
  }, []);

  const filtered = (reviews ?? []).filter((r) =>
    r.repo.toLowerCase().includes(query.toLowerCase()),
  );

  return (
    <div>
      <PageHeader
        title="Review History"
        subtitle="Every review CodeSage has run, newest first."
        actions={
          <button onClick={load} className="btn-ghost" disabled={refreshing}>
            <RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} /> Refresh
          </button>
        }
      />

      <div className="mb-4 relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-faint" />
        <input
          className="input pl-9"
          placeholder="Filter by repository…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      <Card>
        {reviews === null ? (
          <div className="flex items-center justify-center gap-2 py-16 text-sm text-ink-faint">
            <Spinner /> Loading reviews…
          </div>
        ) : error ? (
          <EmptyState title="Could not load reviews">{error}</EmptyState>
        ) : filtered.length === 0 ? (
          <EmptyState icon={<HistoryIcon className="h-6 w-6" />} title="No reviews yet">
            Run your first review from the console to see it here.
          </EmptyState>
        ) : (
          <div className="divide-y divide-line">
            {filtered.map((r) => (
              <ReviewRow key={r.id} review={r} />
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
