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
  const added = mockRunsAnswer(method, p, body);
  if (added !== undefined) return added;
  throw new Error(`no mock for ${method} ${p}`);
}

// ---- Added by 2e: the Run and Review and push screens ---------------------------------------------------------------
// Every number below is invented for the mock, not measured. Five runs, each there to show one thing. The run from
// above (`done`) keeps its own answer for GET; its review and its push answer here too.
export const mockRunIds = {
  done: RUN,
  advancing: "run-20261112T090100Z-mock02", // every GET moves one step on, from pull to review, then it is done
  failed: "run-20261112T090200Z-mock03", // `models` failed; Resume starts it moving on again
  fallback: "run-20261112T090300Z-mock04", // done, with the pull on the Bolt fallback
  partialPush: "run-20261112T090400Z-mock05", // done; the first push stops part way, the second finishes it
} as const;

const MOCK_STEPS = [
  "pull", "indexes", "first_pass", "slice", "models", "score", "communities", "summaries", "stage", "review", "push",
] as const;
const MOCK_AUTO_STEPS = 10; // pull to review run by themselves; push waits for a person
const MOCK_STEP_COUNTS: Record<string, Record<string, number>> = {
  pull: { nodes: 412, relationships: 1630 },
  indexes: { indexes: 4 },
  first_pass: { embeddings: 412, summaries: 96 },
  slice: { nodes: 412, relationships: 1630 },
  models: { models: 3 },
  score: { accounts_scored: 96, factors: 5 },
  communities: { communities: 7 },
  summaries: { summaries: 7 },
  stage: { rows: 111 },
  review: { alerts: 12, communities: 7 },
};
const MOCK_T0 = Date.parse("2026-11-12T09:00:00Z");
const mockIso = (ms: number) => new Date(ms).toISOString();

// `done` steps finished, then one that failed (with its note) or one still running, then the rest waiting.
function mockSteps(done: number, opts: { fail?: string; running?: boolean } = {}): T.Step[] {
  return MOCK_STEPS.map((name, i): T.Step => {
    const counts = MOCK_STEP_COUNTS[name] ?? {};
    const started = MOCK_T0 + i * 4000;
    if (i < done) return { name, status: "done", started_at: mockIso(started), finished_at: mockIso(started + 2500 + i * 700), counts };
    if (i === done && opts.fail) {
      return { name, status: "failed", started_at: mockIso(started), finished_at: mockIso(started + 3100), counts: {}, note: opts.fail };
    }
    if (i === done && opts.running && name !== "push") {
      const half = Object.fromEntries(Object.entries(counts).map(([k, v]) => [k, Math.floor(v / 2)]));
      return { name, status: "running", started_at: new Date().toISOString(), counts: half };
    }
    return { name, status: "pending", counts: {} };
  });
}

function mockManifest(id: string, done: number, extra: Partial<T.RunManifest> & { fail?: string } = {}): T.RunManifest {
  const { fail, ...rest } = extra;
  const finished = done >= MOCK_AUTO_STEPS;
  return {
    run_id: id, recipe_hash: "def456", created_at: "2026-11-12T09:00:00Z",
    finished_at: finished ? mockIso(MOCK_T0 + 45000) : null,
    status: fail ? "failed" : finished ? "done" : "running",
    routes: { pull: "relate" }, fallbacks: [], steps: mockSteps(Math.min(done, MOCK_AUTO_STEPS), { fail, running: !fail && !finished }),
    push: [], counts: {}, ...rest,
  };
}

const mockPolls = new Map<string, number>(); // GETs answered per advancing run
const mockPushes = new Map<string, number>(); // pushes answered per run
/** Forget how far every mock run has come, so a test starts from the first answer. */
export function resetMockRuns() { mockPolls.clear(); mockPushes.clear(); }

function mockRunAnswer(id: string): T.RunManifest | undefined {
  if (id === mockRunIds.advancing || (id === mockRunIds.failed && mockPolls.has(id))) {
    const n = (mockPolls.get(id) ?? 0) + 1;
    mockPolls.set(id, n);
    return mockManifest(id, n - 1);
  }
  if (id === mockRunIds.failed) {
    return mockManifest(id, 4, { fail: "The model container ran out of memory while training TGN. Resume starts again from this step." });
  }
  if (id === mockRunIds.fallback) {
    return mockManifest(id, MOCK_AUTO_STEPS, {
      routes: { pull: "bolt" },
      fallbacks: [{ step: "pull", route: "bolt", reason: "The Query API could not be reached: connection refused at localhost:17474.", at: "2026-11-12T09:00:01Z" }],
    });
  }
  if (id === mockRunIds.partialPush) return mockManifest(id, MOCK_AUTO_STEPS);
  return undefined;
}

// The plan lists nodes and relationships mixed, as the lab may give them; the screen groups them.
const MOCK_PLAN: T.PushEntity[] = [
  { entity: "Alert", kind: "node", total: 12, created: 12, updated: 0, skipped: 0, batches: 1, landed: false },
  { entity: "HAS_ALERT", kind: "relationship", total: 12, created: 12, updated: 0, skipped: 0, batches: 1, landed: false },
  { entity: "Account", kind: "node", total: 96, created: 0, updated: 96, skipped: 0, batches: 1, landed: false },
  { entity: "IN_COMMUNITY", kind: "relationship", total: 31, created: 31, updated: 0, skipped: 0, batches: 1, landed: false },
  { entity: "RiskCommunity", kind: "node", total: 7, created: 7, updated: 0, skipped: 0, batches: 1, landed: false },
];

function mockReview(id: string): T.Review {
  const partial = id === mockRunIds.partialPush;
  return {
    run_id: id,
    plan: MOCK_PLAN,
    plan_text: [
      "relate push --dry-run  (nothing was sent)",
      "node          Alert           12 rows  1 batch",
      "relationship  HAS_ALERT       12 rows  1 batch",
      "node          Account         96 rows  1 batch",
      "relationship  IN_COMMUNITY    31 rows  1 batch",
      "node          RiskCommunity    7 rows  1 batch",
    ].join("\n"),
    delta: {
      new_alerts: 12, new_communities: 7, accounts_scored: 96,
      score_changes: { "0.0-0.1": 41, "0.1-0.2": 28, "0.2-0.3": 15, "0.3-0.4": 8, "0.4-0.5": 3, "0.5-1.0": 1 },
      missing_remote_keys: partial ? 3 : 0, sampled: 50, vectors: 412, dispositions: 4, bytes_estimate: 2480000,
    },
  };
}

// Like `apply_push`: without confirm it refuses and sends nothing.
function mockPush(id: string, body: unknown): T.PushResult {
  if ((body as { confirm?: boolean } | undefined)?.confirm !== true) throw new Error("Push refused: confirm is not set. Nothing was sent.");
  const attempt = (mockPushes.get(id) ?? 0) + 1;
  mockPushes.set(id, attempt);
  const byName = (name: string) => MOCK_PLAN.find((e) => e.entity === name)!;
  const landed = (name: string): T.PushEntity => ({ ...byName(name), landed: true });
  if (id === mockRunIds.partialPush && attempt === 1) {
    return {
      run_id: id, pushed: false, resumed: false,
      entities: [
        landed("Alert"), landed("Account"),
        { ...byName("RiskCommunity"), created: 0, landed: false, error: "Batch 1 (rows 1 to 7, MERGE on :RiskCommunity) failed: the Query API answered 503." },
        { ...byName("HAS_ALERT"), created: 0, landed: false, error: "Not sent: the push stopped at an earlier batch." },
        { ...byName("IN_COMMUNITY"), created: 0, landed: false, error: "Not sent: the push stopped at an earlier batch." },
      ],
      message: "The push stopped at RiskCommunity. 2 of 5 entities landed. Nothing was deleted.",
    };
  }
  const resumed = attempt > 1;
  return {
    run_id: id, pushed: true, resumed,
    entities: ["Alert", "Account", "RiskCommunity", "HAS_ALERT", "IN_COMMUNITY"].map(landed),
    message: resumed ? "Resumed: pushed the 3 entities that had not landed. LabRun written last." : "Pushed 5 entities. LabRun written last.",
  };
}

// The Run, Review and Resume routes for the runs above; undefined for anything else, so it still fails loudly.
function mockRunsAnswer(method: string, p: string, body: unknown): unknown {
  const m = /^\/api\/runs\/([^/]+)(?:\/(review|push|resume))?$/.exec(p);
  if (!m || !(Object.values(mockRunIds) as string[]).includes(m[1])) return undefined;
  const [, id, tail] = m;
  const hasReview = id === mockRunIds.done || id === mockRunIds.partialPush;
  if (method === "GET" && !tail) return mockRunAnswer(id);
  if (method === "GET" && tail === "review" && hasReview) return mockReview(id);
  if (method === "POST" && tail === "push" && hasReview) return mockPush(id, body);
  if (method === "POST" && tail === "resume" && id === mockRunIds.failed) { mockPolls.set(id, 4); return { run_id: id }; }
  return undefined;
}
