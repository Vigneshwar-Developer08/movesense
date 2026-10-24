/**
 * Base URL for the backend API.
 *
 * When the app is deployed as a single Vercel project, the FastAPI service is
 * reachable on the same origin under `/api`, so the default (empty) base keeps
 * every request same-origin. Set `NEXT_PUBLIC_API_URL` only when the Next.js
 * frontend runs separately from the API (for example `next dev` on :3000 and
 * `uvicorn` on :8000).
 */
const configuredBase = process.env.NEXT_PUBLIC_API_URL?.trim();

export const API_BASE = configuredBase
  ? configuredBase.replace(/\/+$/, "")
  : "";

export type ArtifactFormat = "csv" | "json" | "mat";

interface ErrorBody {
  detail?: string | { message?: string };
}

export function apiErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === "string" && error) return error;
  return fallback;
}

async function responseError(response: Response): Promise<Error> {
  let detail: ErrorBody["detail"];
  try {
    const body = (await response.json()) as ErrorBody;
    detail = body.detail;
  } catch {
    // Non-JSON error (e.g. a proxy page); use the HTTP status below.
  }
  const message =
    typeof detail === "string"
      ? detail
      : detail?.message ?? response.statusText ?? `Request failed (${response.status})`;
  return new Error(message);
}

export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    // Send the HttpOnly session cookie set by POST /api/auth/signin|signup.
    credentials: "include",
    ...init,
  });
  if (!response.ok) throw await responseError(response);
  return (await response.json()) as T;
}

export async function downloadArtifact(
  sessionId: string,
  format: ArtifactFormat,
): Promise<void> {
  const response = await fetch(
    `${API_BASE}/api/sessions/${encodeURIComponent(sessionId)}/export?format=${format}`,
    { credentials: "include" },
  );
  if (!response.ok) throw await responseError(response);

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${sessionId}.${format}`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export type RawArtifactFormat = "raw_csv" | "raw_json" | "raw_bin";

const RAW_EXTENSION: Record<RawArtifactFormat, string> = {
  raw_csv: "csv",
  raw_json: "json",
  raw_bin: "bin",
};

/** Download a raw IMU capture saved by acquisition.record_session. */
export async function downloadRawArtifact(
  sessionId: string,
  format: RawArtifactFormat,
): Promise<void> {
  const response = await fetch(
    `${API_BASE}/api/local/sessions/${encodeURIComponent(sessionId)}/export?format=${format}`,
    { credentials: "include" },
  );
  if (!response.ok) throw await responseError(response);

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${sessionId}_raw.${RAW_EXTENSION[format]}`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
