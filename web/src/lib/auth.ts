// API-key storage for the console. The key lives in this browser's
// localStorage and is sent as a Bearer token on every /api/v1 request.

const STORAGE_KEY = "codesage.apiKey";

/** Fired when the API rejects a request for a missing/invalid key. */
export const AUTH_REQUIRED_EVENT = "codesage:auth-required";

export function getApiKey(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setApiKey(key: string | null): void {
  try {
    if (key) localStorage.setItem(STORAGE_KEY, key);
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* storage unavailable (private mode); the key just won't persist */
  }
}

export function authHeaders(): Record<string, string> {
  const key = getApiKey();
  return key ? { Authorization: `Bearer ${key}` } : {};
}

export function notifyAuthRequired(): void {
  window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT));
}
