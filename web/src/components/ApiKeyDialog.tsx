import { useEffect, useRef, useState, type FormEvent } from "react";
import { KeyRound, X } from "lucide-react";
import { getApiKey, setApiKey } from "../lib/auth";

/** Modal for entering the API key the console sends as a Bearer token. */
export function ApiKeyDialog({
  open,
  reason,
  onClose,
}: {
  open: boolean;
  reason?: string;
  onClose: () => void;
}) {
  const [value, setValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const hasKey = !!getApiKey();

  useEffect(() => {
    if (!open) return;
    setValue("");
    inputRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const save = (e: FormEvent) => {
    e.preventDefault();
    if (!value.trim()) return;
    setApiKey(value.trim());
    // Reload so every view refetches with the new key.
    window.location.reload();
  };

  const clear = () => {
    setApiKey(null);
    window.location.reload();
  };

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <form
        role="dialog"
        aria-modal="true"
        aria-labelledby="api-key-title"
        onSubmit={save}
        onClick={(e) => e.stopPropagation()}
        className="card w-full max-w-md p-6"
      >
        <div className="mb-4 flex items-start justify-between gap-4">
          <div className="flex items-center gap-3">
            <KeyRound className="h-5 w-5 text-brand-400" />
            <h2 id="api-key-title" className="text-base font-semibold text-ink">
              API key
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="text-ink-faint hover:text-ink"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <p className="mb-4 text-sm text-ink-muted">
          {reason ?? "The CodeSage API requires a key. It is stored in this browser only."}
        </p>
        <label htmlFor="api-key-input" className="label">
          Key
        </label>
        <input
          id="api-key-input"
          ref={inputRef}
          type="password"
          autoComplete="off"
          className="input font-mono"
          placeholder={hasKey ? "Enter a new key to replace the saved one" : "Paste your key"}
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        <div className="mt-5 flex justify-end gap-2">
          {hasKey && (
            <button type="button" onClick={clear} className="btn-ghost">
              Forget key
            </button>
          )}
          <button type="submit" className="btn-primary" disabled={!value.trim()}>
            Save key
          </button>
        </div>
      </form>
    </div>
  );
}
