import { useState } from "react";
import { Database, FileCode2, Layers, Play } from "lucide-react";
import { api, ApiError } from "../lib/api";
import { Card, CardHeader, PageHeader, Spinner, Stat } from "../components/ui";

export function IngestPage() {
  const [repo, setRepo] = useState("");
  const [path, setPath] = useState("");
  const [replace, setReplace] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ repo: string; files: number; chunks: number } | null>(
    null,
  );

  async function ingest() {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await api.ingest({ repo: repo.trim(), path: path.trim(), replace }));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="RAG Ingestion"
        subtitle="Index a codebase into pgvector so reviewers can cite real surrounding code."
      />
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader
            title="Ingest a repository"
            subtitle="Chunks source files, embeds them, and upserts vectors."
            icon={<Database className="h-4 w-4" />}
          />
          <div className="space-y-4 p-5">
            <div>
              <label className="label">Repository name</label>
              <input
                className="input font-mono"
                value={repo}
                onChange={(e) => setRepo(e.target.value)}
                placeholder="owner/name"
              />
            </div>
            <div>
              <label className="label">Filesystem path (on the API host)</label>
              <input
                className="input font-mono"
                value={path}
                onChange={(e) => setPath(e.target.value)}
                placeholder="/app  ·  /path/to/repo"
              />
              <p className="mt-1.5 text-xs text-ink-faint">
                Path is resolved inside the API container. In Docker, the app source lives at{" "}
                <code className="rounded bg-surface-3 px-1 font-mono">/app</code>.
              </p>
            </div>
            <label className="flex items-center gap-2 text-sm text-ink-muted">
              <input
                type="checkbox"
                checked={replace}
                onChange={(e) => setReplace(e.target.checked)}
                className="h-4 w-4 rounded border-line accent-brand-600"
              />
              Replace existing chunks for this repo first
            </label>
            {error && (
              <p className="rounded-lg border border-rose-500/30 bg-rose-500/5 px-3 py-2 text-sm text-rose-400">
                {error}
              </p>
            )}
            <button
              onClick={ingest}
              disabled={busy || !repo.trim() || !path.trim()}
              className="btn-primary w-full"
            >
              {busy ? <Spinner /> : <Play className="h-4 w-4" />}
              {busy ? "Ingesting…" : "Start ingestion"}
            </button>
          </div>
        </Card>

        <div className="space-y-6">
          {result ? (
            <>
              <div className="grid grid-cols-2 gap-3">
                <Stat
                  label="Files indexed"
                  value={result.files}
                  icon={<FileCode2 className="h-4 w-4" />}
                />
                <Stat
                  label="Chunks stored"
                  value={result.chunks}
                  icon={<Layers className="h-4 w-4" />}
                  tone="text-brand-400"
                />
              </div>
              <Card className="border-emerald-500/30 bg-emerald-500/5 p-5 text-sm">
                <p className="font-medium text-emerald-500">
                  Ingested <span className="font-mono">{result.repo}</span> successfully.
                </p>
                <p className="mt-1 text-ink-muted">
                  Reviewers will now retrieve context from this codebase via the RAG pipeline.
                </p>
              </Card>
            </>
          ) : (
            <Card className="p-6">
              <div className="grid-backdrop rounded-lg p-8">
                <h3 className="text-sm font-semibold text-ink">How retrieval works</h3>
                <ol className="mt-3 space-y-2 text-sm text-ink-muted">
                  <li>1. Walk the tree, skipping vendored/build directories.</li>
                  <li>2. Chunk each source file with line spans.</li>
                  <li>
                    3. Embed via Voyage (or a deterministic local embedder when no key is set).
                  </li>
                  <li>4. Upsert vectors into pgvector for similarity search at review time.</li>
                </ol>
              </div>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
