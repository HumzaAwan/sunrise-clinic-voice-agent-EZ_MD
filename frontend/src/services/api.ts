async function read<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => read<import("../types/clinic").VoiceHealth>("/api/health"),
  openSession: () =>
    read<{ available: boolean; url?: string; token?: string; room?: string; reason?: string }>("/api/sessions", {
      method: "POST",
    }),
  overview: () => read<import("../types/clinic").Overview>("/api/overview"),
  requests: () => read<import("../types/clinic").RequestsPayload>("/api/requests"),
  sessions: () =>
    read<{
      sessions: {
        session_id: string;
        phase: string;
        verified: boolean;
        started_at: string | null;
        lines: { who: string; text: string; at: string }[];
      }[];
    }>("/api/sessions"),
  settings: () => read<{ timezone: string; recording_retention_days: number; transcript_retention_days: number; office_open: boolean; now: string; database: string; docker: string }>("/api/settings"),
  runEvaluations: () =>
    read<{ passed: number; failed: number; results: import("../types/clinic").EvalResult[] }>("/api/evaluations/run", { method: "POST" }),
  evaluations: () => read<{ runs: import("../types/clinic").EvalRun[] }>("/api/evaluations"),
  setClock: (override: string | null) =>
    read<{ office_open: boolean; now: string }>("/api/admin/clock", {
      method: "POST",
      body: JSON.stringify({ override }),
    }),
};
