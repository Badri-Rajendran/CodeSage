import { useCallback, useEffect, useState, type ReactNode } from "react";
import { NavLink } from "react-router-dom";
import {
  Activity,
  Boxes,
  Database,
  FlaskConical,
  Gauge,
  History,
  KeyRound,
  Moon,
  ShieldQuestion,
  Sun,
} from "lucide-react";
import { useTheme } from "../lib/theme";
import { api, type ServiceInfo } from "../lib/api";
import { AUTH_REQUIRED_EVENT, getApiKey } from "../lib/auth";
import { ApiKeyDialog } from "./ApiKeyDialog";

const NAV = [
  { to: "/", label: "Review Console", icon: Activity, end: true },
  { to: "/history", label: "History", icon: History },
  { to: "/approvals", label: "Approval Queue", icon: ShieldQuestion },
  { to: "/telemetry", label: "Cost & Telemetry", icon: Gauge },
  { to: "/ingest", label: "RAG Ingestion", icon: Database },
  { to: "/eval", label: "Eval & Regression", icon: FlaskConical },
];

export function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar />
        <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-8">{children}</main>
      </div>
    </div>
  );
}

function Sidebar() {
  return (
    <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-line bg-surface-2 lg:flex">
      <div className="flex items-center gap-2.5 px-5 py-5">
        <div className="grid h-9 w-9 place-items-center rounded-lg bg-gradient-to-br from-brand-400 to-brand-700 text-white shadow-lg shadow-brand-900/30">
          <Boxes className="h-5 w-5" />
        </div>
        <div>
          <p className="text-sm font-semibold leading-tight text-ink">CodeSage</p>
          <p className="text-[11px] leading-tight text-ink-faint">Agentic Reviewer</p>
        </div>
      </div>
      <nav className="flex-1 space-y-1 px-3 py-2">
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                isActive
                  ? "bg-brand-600/10 text-brand-400"
                  : "text-ink-muted hover:bg-surface-3 hover:text-ink"
              }`
            }
          >
            <Icon className="h-4 w-4" />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="px-5 py-4 text-[11px] text-ink-faint">
        Multi-agent PR review · LangGraph · pgvector RAG
      </div>
    </aside>
  );
}

function Topbar() {
  const { theme, toggle } = useTheme();
  const [info, setInfo] = useState<ServiceInfo | null>(null);
  const [down, setDown] = useState(false);
  const [keyDialog, setKeyDialog] = useState<{ open: boolean; reason?: string }>({
    open: false,
  });
  const closeKeyDialog = useCallback(() => setKeyDialog({ open: false }), []);

  useEffect(() => {
    const onAuthRequired = () =>
      setKeyDialog({
        open: true,
        reason: getApiKey()
          ? "The API rejected the saved key. Enter a valid one."
          : "The CodeSage API requires a key. It is stored in this browser only.",
      });
    window.addEventListener(AUTH_REQUIRED_EVENT, onAuthRequired);
    return () => window.removeEventListener(AUTH_REQUIRED_EVENT, onAuthRequired);
  }, []);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .meta()
        .then((i) => alive && (setInfo(i), setDown(false)))
        .catch(() => alive && setDown(true));
    load();
    const t = setInterval(load, 10_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  return (
    <header className="sticky top-0 z-20 flex h-14 items-center justify-between border-b border-line bg-surface/80 px-6 backdrop-blur">
      <div className="flex items-center gap-3">
        <span className="lg:hidden">
          <Boxes className="h-5 w-5 text-brand-400" />
        </span>
        <StatusPill info={info} down={down} />
      </div>
      <div className="flex items-center gap-3">
        {info && (
          <span className="hidden font-mono text-xs text-ink-faint sm:inline">
            {info.model}
          </span>
        )}
        {info?.auth_required && (
          <button
            onClick={() => setKeyDialog({ open: true })}
            aria-label="Set API key"
            title={getApiKey() ? "API key saved" : "Set API key"}
            className={`grid h-9 w-9 place-items-center rounded-lg border bg-surface hover:text-ink ${
              getApiKey() ? "border-line text-ink-muted" : "border-amber-500/40 text-amber-400"
            }`}
          >
            <KeyRound className="h-4 w-4" />
          </button>
        )}
        <button
          onClick={toggle}
          aria-label="Toggle theme"
          className="grid h-9 w-9 place-items-center rounded-lg border border-line bg-surface text-ink-muted hover:text-ink"
        >
          {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </button>
      </div>
      <ApiKeyDialog open={keyDialog.open} reason={keyDialog.reason} onClose={closeKeyDialog} />
    </header>
  );
}

function StatusPill({ info, down }: { info: ServiceInfo | null; down: boolean }) {
  if (down) {
    return (
      <span className="inline-flex items-center gap-2 rounded-full border border-rose-500/30 bg-rose-500/10 px-3 py-1 text-xs font-medium text-rose-400">
        <span className="h-2 w-2 rounded-full bg-rose-500" />
        API unreachable
      </span>
    );
  }
  const live = info?.llm_mode === "live";
  return (
    <span className="inline-flex items-center gap-2 rounded-full border border-line bg-surface px-3 py-1 text-xs font-medium text-ink-muted">
      <span
        className={`h-2 w-2 rounded-full ${live ? "bg-emerald-500" : "bg-amber-500"} ${
          info ? "animate-pulse" : ""
        }`}
      />
      {info ? (live ? "Live · Claude connected" : "Stub mode") : "Connecting…"}
    </span>
  );
}
