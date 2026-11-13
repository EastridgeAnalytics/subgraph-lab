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

  // Added by 1d. Every number below is invented for the mock, not measured.
  // A connection that fails its test: the mock answers this when the URI contains "unreachable".
  connectFail: {
    ok: false, message: "Could not reach unreachable.example.com:7687. Check the address, or that Aura is running.",
  } satisfies T.ConnectResult,
  // A schema or Cypher slice has no hits and no hops.
  estimateSchema: {
    mode: "schema", hits: [], hops: null, nodes: 9800, relationships: 41000, memory_bytes: 32200000,
    over_cap: false, cap: 200000,
  } satisfies T.SliceEstimate,
  // A search slice past the cap: the mock answers this when the request asks for more than four hops.
  estimateOverCap: {
    mode: "search",
    hits: [
      { key: "A0011", rrf: 0.0328, found_by: ["vector", "fulltext"] },
      { key: "A0472", rrf: 0.0164, found_by: ["vector"] },
    ],
    hops: 5, nodes: 480000, relationships: 1900000, memory_bytes: 1513000000, over_cap: true, cap: 200000,
    note: "Five hops reach more than the cap. Lower k or the hops.",
  } satisfies T.SliceEstimate,
  code: {
    recipe_yaml: "name: default\ngraph:\n  account_label: Account\npull:\n  mode: schema\n  labels: []\n  types: []\n",
    python: "import lab\n\nrun = lab.run()  # the code the lab ran for this run\n",
  } satisfies T.ShowCode,
};

type Body = { uri?: string; recipe?: T.Recipe; search?: T.SearchSpec | null } | undefined;

function estimateFor(body: Body): T.SliceEstimate {
  const search = body?.search ?? body?.recipe?.search;
  if (!search) return mockData.estimateSchema;
  return (search.hops ?? 0) > 4 ? mockData.estimateOverCap : mockData.estimate;
}

export async function mockHandler(method: string, path: string, body?: unknown): Promise<unknown> {
  const p = path.split("?")[0];
  if (method === "GET" && p === "/api/health") return mockData.health;
  if (method === "POST" && (p === "/api/connect/test" || p === "/api/connect/use")) {
    return (body as Body)?.uri?.includes("unreachable") ? mockData.connectFail : mockData.connect;
  }
  if (method === "GET" && p === "/api/connect") return mockData.connect;
  if (method === "POST" && p === "/api/slice/estimate") return estimateFor(body as Body);
  if (method === "GET" && /^\/api\/runs\/[^/]+\/code$/.test(p)) return mockData.code;
  if (method === "POST" && (p === "/api/slice/run" || p === "/api/runs")) return { run_id: RUN };
  if (method === "GET" && p === "/api/runs") return { runs: [mockData.run] };
  if (method === "GET" && p === `/api/runs/${RUN}`) return mockData.run;
  throw new Error(`no mock for ${method} ${p}`);
}
