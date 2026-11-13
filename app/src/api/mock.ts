// Mock answers for the app's tests and for working on screens without the lab running. Shapes follow types.ts.
// Screen builders add the routes their screen needs; an unknown route throws so a missing mock is loud.
import type * as T from "./types";

const RUN = "run-20261112T090000Z-mock01";

export const mockData = {
  health: { ok: true, version: "0.1.0", relate: "1.0.1" } satisfies T.Health,
  connect: {
    ok: true, neo4j_version: "2026.08.1", edition: "community", query_uri: "http://localhost:17474",
    pull_route: "relate", message: "Connected. relate pull will use the Query API.",
  } satisfies T.ConnectResult,
  estimate: {
    mode: "search", hits: [{ key: "A0011", rrf: 0.0328, found_by: ["vector", "fulltext"] }], hops: 2,
    nodes: 412, relationships: 1630, memory_bytes: 1332000, over_cap: false, cap: 200000,
  } satisfies T.SliceEstimate,
  run: {
    run_id: RUN, recipe_hash: "abc123", created_at: "2026-11-12T09:00:00Z", status: "done", routes: { pull: "relate" },
    fallbacks: [], steps: [{ name: "pull", status: "done", counts: { nodes: 412, relationships: 1630 } }], push: [],
    counts: {},
  } satisfies T.RunManifest,
};

export async function mockHandler(method: string, path: string, _body?: unknown): Promise<unknown> {
  const p = path.split("?")[0];
  if (method === "GET" && p === "/api/health") return mockData.health;
  if (method === "POST" && p === "/api/connect/test") return mockData.connect;
  if (method === "POST" && p === "/api/slice/estimate") return mockData.estimate;
  if (method === "POST" && (p === "/api/slice/run" || p === "/api/runs")) return { run_id: RUN };
  if (method === "GET" && p === "/api/runs") return { runs: [mockData.run] };
  if (method === "GET" && p === `/api/runs/${RUN}`) return mockData.run;
  throw new Error(`no mock for ${method} ${p}`);
}
