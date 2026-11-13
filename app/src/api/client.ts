// One client for every screen. With the mock switch on, it answers from src/api/mock.ts and never calls the lab.
// The switch: VITE_MOCK_API=1 at build time, or ?mock=1 in the address bar, or setMock(true) in tests.
import type * as T from "./types";
import { mockHandler } from "./mock";

let mockOverride: boolean | null = null;
export function setMock(on: boolean | null) { mockOverride = on; }
export function isMock(): boolean {
  if (mockOverride !== null) return mockOverride;
  if (import.meta.env.VITE_MOCK_API === "1") return true;
  return typeof window !== "undefined" && new URLSearchParams(window.location.search).get("mock") === "1";
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function call<R>(method: "GET" | "POST", path: string, body?: unknown): Promise<R> {
  if (isMock()) return mockHandler(method, path, body) as Promise<R>;
  const res = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* not JSON */ }
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<R>;
}

export const api = {
  health: () => call<T.Health>("GET", "/api/health"),
  connectTest: (b: T.ConnectTest) => call<T.ConnectResult>("POST", "/api/connect/test", b),
  sliceEstimate: (b: T.SliceRequest) => call<T.SliceEstimate>("POST", "/api/slice/estimate", b),
  sliceRun: (b: T.SliceRequest) => call<T.RunStarted>("POST", "/api/slice/run", b),
  models: () => call<T.ModelsList>("GET", "/api/models"),
  startRun: (recipe: T.Recipe) => call<T.RunStarted>("POST", "/api/runs", { recipe }),
  runs: () => call<T.RunsList>("GET", "/api/runs"),
  run: (id: string) => call<T.RunManifest>("GET", `/api/runs/${id}`),
  resume: (id: string) => call<T.RunStarted>("POST", `/api/runs/${id}/resume`),
  accounts: (id: string) => call<T.AccountsPage>("GET", `/api/runs/${id}/results/accounts`),
  communities: (id: string) => call<T.CommunitiesPage>("GET", `/api/runs/${id}/results/communities`),
  community: (id: string, key: string) => call<T.CommunityDetail>("GET", `/api/runs/${id}/results/community/${key}`),
  review: (id: string) => call<T.Review>("GET", `/api/runs/${id}/review`),
  push: (id: string, confirm: boolean) => call<T.PushResult>("POST", `/api/runs/${id}/push`, { confirm }),
  disposition: (id: string, b: T.DispositionRequest) => call<T.DispositionResult>("POST", `/api/runs/${id}/dispositions`, b),
  timeline: (id: string, asOf?: string) =>
    call<T.Timeline>("GET", `/api/runs/${id}/timeline${asOf ? `?as_of=${encodeURIComponent(asOf)}` : ""}`),
  code: (id: string) => call<T.ShowCode>("GET", `/api/runs/${id}/code`),
};
