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
    mode: "search", hits: [{ key: "A0011", rrf: 0.0328, found_by: ["vector", "fulltext"] }], hops: 1,
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
  if (method === "GET" && /^\/api\/runs\/[^/]+\/timeline$/.test(p)) return mockTimelineAnswer(path);
  const added = mockRunsAnswer(method, p, body);
  if (added !== undefined) return added;
  const shown = mockModelsAndResults(method, path, body);
  if (shown !== undefined) return shown;
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

// ---- Added by 3e: the Timeline screen ---------------------------------------------------------------------------------
// 45 days from 2026-09-01, one point a day except 2026-09-07, which has no payments (so a day can be missing from the
// answer, as in the lab's). Every number below is invented for the mock, not measured.
export const MOCK_TIMELINE_START = "2026-09-01";
export const MOCK_TIMELINE_DAYS = 45;
const MOCK_TIMELINE_GAP = 6; // the day index with no payments
const MOCK_TIMELINE_BOUNDS = { start: "2026-09-01T03:12:00Z", end: "2026-10-15T21:40:00Z" };

const mockDayOf = (i: number) => new Date(Date.parse(`${MOCK_TIMELINE_START}T00:00:00Z`) + i * 86_400_000).toISOString().slice(0, 10);

/** The lab's `GET /timeline`: one point per day with payments, up to `asOf` (a bare date means the end of that day;
 *  none means to the end). `start` and `end` stay the bounds of the whole store whatever `asOf` is. */
export function mockTimeline(asOf?: string | null): T.Timeline {
  const cutoff = asOf ? Date.parse(asOf.length === 10 ? `${asOf}T23:59:59Z` : asOf) : null;
  if (cutoff !== null && Number.isNaN(cutoff)) throw new Error(`as_of must be an ISO-8601 date or datetime, got ${asOf}`);
  const points: T.TimelinePoint[] = [];
  for (let i = 0; i < MOCK_TIMELINE_DAYS; i++) {
    const at = `${mockDayOf(i)}T00:00:00Z`;
    if (i === MOCK_TIMELINE_GAP || (cutoff !== null && Date.parse(at) > cutoff)) continue;
    const payments = 30 + ((i * 37) % 29);
    points.push({
      at, payments, accounts: Math.min(payments * 2, 20 + ((i * 11) % 25)),
      amount: Math.round(payments * (180 + ((i * 13) % 40)) * 100) / 100,
    });
  }
  const as_of = cutoff === null ? null : new Date(cutoff).toISOString().replace(".000Z", "Z");
  return { ...MOCK_TIMELINE_BOUNDS, as_of, points };
}

// The route for any run id: `?as_of=` in the path is read here, since mockHandler drops the query before it matches.
function mockTimelineAnswer(path: string): T.Timeline {
  return mockTimeline(new URLSearchParams(path.split("?")[1] ?? "").get("as_of"));
}

// ---- Added by 3d: the Models and Results screens ---------------------------------------------------------------------
// Every number below is invented for the mock, not measured. The factors, their thresholds and their three weight tiers
// (0.65 enough alone, 0.20 supporting, 0.05 unproven) are the ones factors.yaml ships; the model list is the one the lab
// builds from its own settings. `GET /api/models`, `GET /api/models/{name}/source` and the results routes answer for the
// five runs above. The `failed` run has no results, like a real run that stopped before `score`. The results routes
// take `limit` and `offset` like the lab's, over a list longer than one page.
const MOCK_FACTORS: T.Factor[] = [
  { id: "shared_device_5plus", name: "Five or more accounts on one device", kind: "cypher", query: "shared_device.cypher",
    threshold: 5, weight: 0.2,
    description: "Accounts that share a device with at least four others. Shared hardware is the hardest link to fake." },
  { id: "new_accounts_shared_identifier", name: "New accounts sharing an identifier", kind: "cypher",
    query: "new_accounts_shared_identifier.cypher", threshold: 3, weight: 0.65,
    description: "Three or more accounts opened within 30 days of each other that share a device, an email or an address." },
  { id: "in_and_out_within_a_day", name: "Money in and out of similar size within a day", kind: "cypher",
    query: "in_and_out_within_a_day.cypher", threshold: 3, weight: 0.65,
    description: "Three or more outgoing payments within 24 hours of an incoming payment of about the same amount. Pass-through." },
  { id: "just_under_threshold", name: "Payments just under 10,000", kind: "cypher", query: "just_under_threshold.cypher",
    threshold: 3, weight: 0.65,
    description: "Three or more payments between 9,000 and 9,999.99 in or out of the account. Structuring." },
  { id: "fan_in_new_accounts", name: "Fan-in from new accounts", kind: "cypher", query: "fan_in_new_accounts.cypher",
    threshold: 5, weight: 0.2,
    description: "Payments in from five or more different accounts that were 30 days old or less when they paid." },
  { id: "velocity_anomaly", name: "Outflow far above the account's own forecast", kind: "python",
    function: "lab.scoring.velocity:score", weight: 0.05,
    description: "Outflow against the account's own statsforecast forecast. Written by the velocity example." },
  { id: "tgn", name: "Temporal model score", kind: "model", source: "examples/tgn", weight: 0.05,
    description: "The TGN score per account from timed payment events. Written by the TGN example." },
];
// The community score is a noisy-or too (decision 45): 1 - the product of (1 - weight x part), over the five parts.
const MOCK_COMMUNITY_WEIGHTS = { member_risk: 0.65, shared_identifiers: 0.2, cycles: 0.2, new_accounts: 0.2, speed: 0.2 };

export const mockModels: T.ModelsList = {
  models: [
    { name: "node2vec", kind: "model", code_path: "examples/node2vec",
      description: "Embeds every node from its neighbours (PyG's Node2Vec). The embeddings feed search and similar accounts." },
    { name: "tgn", kind: "model", code_path: "examples/tgn",
      description: "Scores each account from its timed payment events (PyG's TGN example). Lands as the tgn risk factor." },
    { name: "communities", kind: "model", code_path: "examples/networkx",
      description: "Finds communities in the slice, by payments and shared identifiers: connected components, then seeded Louvain (NetworkX), and scores them." },
    { name: "velocity", kind: "model", code_path: "examples/velocity",
      description: "Compares each account's outflow with its own forecast (statsforecast). Lands as the velocity_anomaly risk factor." },
    ...MOCK_FACTORS.map((f): T.ModelInfo => ({
      name: f.id, kind: "factor", description: f.description ?? f.name, factor: f,
      code_path: f.kind === "cypher" ? `factors/${f.query}` : f.kind === "python" ? "lab/scoring/velocity.py" : f.source ?? "",
    })),
  ],
  factor_version: "9c1e4a7b02d3",
  policy: {
    version: "1", combine: "noisy_or", alert_threshold: 0.6, window: "P30D",
    community: { min_size: 3, combine: "noisy_or", weights: MOCK_COMMUNITY_WEIGHTS },
  },
};

// Communities, best score first. Each one is there to show something on Results.
export const mockCommunityKeys = {
  ring: "c3f9a1d2e4", // the top one: written by the API, every check passed
  agent: "c71b08ae55", // written by a connected agent
  replaced: "c5a2917b3c", // a model's text failed a check, so the template took its place
  template: "c0d4e6f812", // no writer was available: the template
  noEvidence: "c9e8b4a6d0", // no evidence object yet: its detail does not load
} as const;

interface MockSeed {
  key: string; size: number; parts: [number, number, number, number, number]; source: T.Summary["source"] | null;
  replaced?: boolean; disposition?: T.Disposition;
  formed: 0 | 1; // `formed_by_identifier` in score_parts: 1 formed by a shared identifier, 0 by payments. A tag, not a part
}
const PART_NAMES = ["member_risk", "shared_identifiers", "cycles", "new_accounts", "speed"] as const;
// Five named communities and then plain ones, 120 in all: more than one page of 100, so Results has a second page.
const MOCK_COMMUNITY_TOTAL = 120;
const SEEDS: MockSeed[] = [
  { key: mockCommunityKeys.ring, size: 7, parts: [0.86, 0.75, 0.6, 0.8, 0.7], source: "api", formed: 1 },
  { key: mockCommunityKeys.agent, size: 5, parts: [0.78, 0.6, 0.5, 0.7, 0.6], source: "agent", formed: 1 },
  { key: mockCommunityKeys.replaced, size: 4, parts: [0.7, 0.55, 0.4, 0.6, 0.5], source: "template", replaced: true, formed: 1 },
  { key: mockCommunityKeys.template, size: 6, parts: [0.6, 0.5, 0.3, 0.55, 0.4], source: "template", disposition: "false_positive", formed: 1 },
  { key: mockCommunityKeys.noEvidence, size: 3, parts: [0.45, 0.3, 0.2, 0.4, 0.3], source: null, formed: 0 },
  ...Array.from({ length: MOCK_COMMUNITY_TOTAL - 5 }, (_, i): MockSeed => {
    const n = i + 1, k = 1 - i / 120, v = (x: number) => Math.round(x * k * 100) / 100; // each one a little lower than the last
    return {
      key: `cf${String(n).padStart(2, "0")}7a3d1e`, size: 3, parts: [v(0.4), v(0.3), v(0.2), v(0.35), v(0.25)], source: null,
      formed: n % 4 === 0 ? 1 : 0,
    };
  }),
];
const round = (x: number, places: number) => Math.round(x * 10 ** places) / 10 ** places;
const seedScore = (s: MockSeed) => round(1 - s.parts.reduce((t, p, i) => t * (1 - p * MOCK_COMMUNITY_WEIGHTS[PART_NAMES[i]]), 1), 3);

// Members are numbered A1001, A1002 ... across the communities in order, so the accounts table and the graphs agree.
const memberKey = (g: number) => `A${String(1001 + g).padStart(4, "0")}`;
const MOCK_ACCOUNT_TOTAL = 230; // more than one page of 100: Results has a second and a third page
const memberScore = (g: number) => (g <= 45 ? round(0.95 - 0.02 * g, 2) : Math.max(0, round(0.05 * (1 - (g - 45) / 200), 4)));
const SEED_START = SEEDS.map((_, i) => SEEDS.slice(0, i).reduce((t, s) => t + s.size, 0));
const membersOf = (i: number) => Array.from({ length: SEEDS[i].size }, (_, j) => memberKey(SEED_START[i] + j));
const seedOfMember = (g: number) => SEEDS.findIndex((s, i) => g >= SEED_START[i] && g < SEED_START[i] + s.size);

const mockEvidenceHash = (key: string) => `${key}${"a1b2c3d4e5f60718".repeat(4)}`.slice(0, 64);

function mockFiredFactors(score: number): { id: string; score: number; weight: number }[] {
  const w = (id: string) => MOCK_FACTORS.find((f) => f.id === id)!.weight;
  const f = (id: string, s: number) => ({ id, score: s, weight: w(id) });
  if (score >= 0.8) return [f("shared_device_5plus", 1), f("new_accounts_shared_identifier", 0.9), f("in_and_out_within_a_day", 1), f("tgn", 0.8)];
  if (score >= 0.6) return [f("shared_device_5plus", 0.8), f("just_under_threshold", 1), f("tgn", 0.6)];
  if (score >= 0.4) return [f("fan_in_new_accounts", 1), f("in_and_out_within_a_day", 0.5)];
  return [f("velocity_anomaly", 0.4)];
}

const MOCK_ACCOUNT_ROWS: T.AccountRow[] = Array.from({ length: MOCK_ACCOUNT_TOTAL }, (_, g): T.AccountRow => {
  const score = memberScore(g);
  const seed = seedOfMember(g);
  return {
    key: memberKey(g), risk_score: score, risk_score_model: Math.min(1, round(score + 0.05, 2)),
    risk_score_rules: Math.max(0, round(score - 0.04, 2)), factors: mockFiredFactors(score),
    community_key: seed >= 0 ? SEEDS[seed].key : null, alert_key: score >= 0.6 ? `al-${memberKey(g)}` : null,
    disposition: g === 2 ? "confirmed" : null,
  };
});

function mockWritten(i: number): string {
  const s = SEEDS[i], shared = Math.min(s.size, 5), cycles = Math.round(s.parts[2] * 5), span = 20 + s.size * 4;
  return [
    `${shared} of the ${s.size} accounts share device D-${4400 + i}, and 2 share one email address. Money moved between members in ${cycles} cycles within ${span} hours.`,
    "That pattern could indicate accounts operated together, with money passed around the group quickly before it leaves.",
    "A family sharing one laptop, or a payroll account paying several people, would look similar. Check who opened these accounts and why the money returns to where it started.",
  ].join("\n\n");
}
function mockTemplate(i: number): string {
  const s = SEEDS[i], shared = Math.min(s.size, 5), cycles = Math.round(s.parts[2] * 5), span = 20 + s.size * 4;
  return `${s.size} accounts form this community, with a risk score of ${seedScore(s).toFixed(2)}. ${shared} of them share device D-${4400 + i}. ` +
    `Money moved between members in ${cycles} cycles over ${span} hours. Built from the evidence; check each figure against it.`;
}
function mockSummaryOf(i: number): T.Summary | null {
  const s = SEEDS[i];
  if (s.source === null) return null;
  const base = { key: s.key, evidence_hash: mockEvidenceHash(s.key) };
  if (s.replaced) {
    return { ...base, text: mockTemplate(i), source: "template", model: null,
      checks: { numbers: false, members: true, verdict_words: true },
      failed_reason: "A number in the text a model wrote (14) is not in the evidence." };
  }
  if (s.source === "template") return { ...base, text: mockTemplate(i), source: "template", model: null, checks: {} };
  return { ...base, text: mockWritten(i), source: s.source, model: s.source === "api" ? "claude-sonnet-5-5" : null,
    checks: { numbers: true, members: true, verdict_words: true } };
}

function mockCommunityRow(i: number): T.CommunityRow {
  const s = SEEDS[i], summary = mockSummaryOf(i);
  return { key: s.key, size: s.size, score: seedScore(s), summary: summary?.text ?? null, summary_source: summary?.source ?? null,
    disposition: s.disposition ?? null };
}

function mockCommunityDetail(i: number): T.CommunityDetail {
  const s = SEEDS[i], members = membersOf(i), start = SEED_START[i];
  const shared = Math.min(s.size, 5), cycles = Math.round(s.parts[2] * 5), span = 20 + s.size * 4;
  const device = `D-${4400 + i}`, email = `e-${i}93b2`;
  const firstAt = "2026-10-03T14:02:00Z";
  const lastAt = new Date(Date.parse(firstAt) + span * 3600_000).toISOString().replace(".000Z", "Z");
  const nodes: T.GraphNode[] = [
    ...members.map((id, j): T.GraphNode => ({ id, label: "Account", props: { risk_score: memberScore(start + j) } })),
    { id: `Device:${device}`, label: "Device", props: { key: device, kind: "device" } },
    { id: `Email:${email}`, label: "Email", props: { key: email, kind: "email" } },
  ];
  const edges: T.GraphEdge[] = [
    ...members.map((id, j): T.GraphEdge => ({
      source: id, target: members[(j + 1) % members.length], type: "TX", props: { payments: 2 + (j % 3), amount: 1200 * (1 + (j % 4)) },
    })),
    ...members.slice(0, shared).map((id): T.GraphEdge => ({ source: id, target: `Device:${device}`, type: "USES_DEVICE", props: {} })),
    ...members.slice(0, 2).map((id): T.GraphEdge => ({ source: id, target: `Email:${email}`, type: "USES_EMAIL", props: {} })),
  ];
  // The five parts, and how the community formed beside them: a tag the lab keeps in score_parts, not a part of the score.
  const parts: Record<string, number> = { ...Object.fromEntries(PART_NAMES.map((n, j) => [n, s.parts[j]])), formed_by_identifier: s.formed };
  const evidence: T.Evidence = {
    subject: "community", key: s.key, run_id: RUN, member_count: s.size, member_keys: members, score: seedScore(s), score_parts: parts,
    shared_identifiers: [
      { kind: "device", key: device, members_sharing: shared },
      { kind: "email", key: email, members_sharing: 2 },
    ],
    money: { inflow: 48200 + i * 1000, outflow: 51900 + i * 1000, internal: 33400, cycles, first_at: firstAt, last_at: lastAt, span_hours: span },
    factors: [
      { factor_id: "shared_device_5plus", name: "Five or more accounts on one device", accounts: shared, evidence_ids: [device] },
      { factor_id: "in_and_out_within_a_day", name: "Money in and out of similar size within a day", accounts: Math.max(1, s.size - 1),
        evidence_ids: members.slice(0, 4).map((m) => `tx-${m}`) },
    ],
    top_members: members.slice(0, 3).map((key, j) => ({ key, score: memberScore(start + j) })),
    comparison: { communities_in_slice: SEEDS.length, score_rank: i + 1, median_size: 3, median_score: 0.41 },
  };
  const similar = [1, 2, 3].map((d) => mockCommunityRow((i + d) % SEEDS.length));
  return { row: mockCommunityRow(i), score_parts: parts, evidence, summary: mockSummaryOf(i), nodes, edges, similar };
}

// A decision, like the lab's: it needs a name, and it says it is staged for the next push.
function mockDisposition(body: unknown): T.DispositionResult {
  const b = body as Partial<T.DispositionRequest> | undefined;
  if (!b?.by?.trim()) throw new Error("a disposition records who made it: `by` is empty");
  if (!b.key || !b.kind || !b.disposition) throw new Error("a disposition needs a kind, a key and a decision");
  return { ok: true, staged: true };
}

// ---- the source route (decision 43) -----------------------------------------------------------------------------------
// Short texts for the mock, not the lab's files. A cypher factor gets Cypher, a python factor and a model get Python.
const MOCK_SHARED_DEVICE = [
  "// Five or more accounts on one device. Returns one row per account on such a device.",
  "// key: the account key; value: how many accounts share the device; evidence_ids: the device key.",
  "// $threshold: the factor's threshold. $keys: a list of account keys to score, or null for every account.",
  "MATCH (a:Account)-[:USES_DEVICE]->(d:Device)<-[:USES_DEVICE]-(other:Account)",
  "WHERE $keys IS NULL OR a.account_id IN $keys",
  "WITH a, d, count(DISTINCT other) + 1 AS sharing",
  "WHERE sharing >= $threshold",
  "RETURN a.account_id AS key, sharing AS value, [d.device_id] AS evidence_ids",
  "",
].join("\n");

function mockSource(name: string): T.SourceFile {
  const info = mockModels.models.find((m) => m.name === name); // the first match, as the lab does: the model before the factor
  if (!info) throw new Error(`no model or factor named '${name}'`);
  const f = info.factor;
  if (info.kind === "factor" && f?.kind === "cypher") {
    const text = f.id === "shared_device_5plus" ? MOCK_SHARED_DEVICE : [
      `// ${f.name}. Mock text: the lab shows the real file here.`,
      "// $threshold: the factor's threshold. $keys: a list of account keys to score, or null for every account.",
      "MATCH (a:Account)",
      "WHERE $keys IS NULL OR a.account_id IN $keys",
      "RETURN a.account_id AS key, 0 AS value, [] AS evidence_ids",
      "",
    ].join("\n");
    return { name, path: info.code_path, language: "cypher", text };
  }
  const path = info.kind === "factor" && f?.kind === "python" ? info.code_path : `${info.code_path}/example.py`;
  const text = [
    `"""${info.description} Mock text: the lab shows the real file here."""`,
    "from __future__ import annotations",
    "",
    "",
    "def run_model(run, current_slice):",
    "    ...",
    "",
  ].join("\n");
  return { name, path, language: "python", text };
}

// ---- paging, like the lab's: `limit` 1 to 1000 (default 100), `offset` from 0 -----------------------------------------
const MOCK_MAX_LIMIT = 1000;
function mockPage<R>(rows: R[], path: string): { total: number; rows: R[] } {
  const q = new URLSearchParams(path.split("?")[1] ?? "");
  const whole = (name: string, fallback: number, min: number, max: number) => {
    const text = q.get(name);
    if (text === null) return fallback;
    const n = Number(text);
    if (!Number.isInteger(n) || n < min || n > max) throw new Error(`${name} must be a whole number from ${min} to ${max}, got ${text}`);
    return n;
  };
  const limit = whole("limit", 100, 1, MOCK_MAX_LIMIT);
  const offset = whole("offset", 0, 0, Number.MAX_SAFE_INTEGER);
  return { total: rows.length, rows: rows.slice(offset, offset + limit) };
}

// GET /api/models, its source route and the results routes; undefined for anything else, so an unknown route still fails loudly.
function mockModelsAndResults(method: string, path: string, body: unknown): unknown {
  const p = path.split("?")[0];
  if (method === "GET" && p === "/api/models") return mockModels;
  const source = /^\/api\/models\/([^/]+)\/source$/.exec(p);
  if (method === "GET" && source) return mockSource(decodeURIComponent(source[1]));
  const m = /^\/api\/runs\/([^/]+)\/(results\/accounts|results\/communities|results\/community\/([^/]+)|dispositions)$/.exec(p);
  if (!m || !(Object.values(mockRunIds) as string[]).includes(m[1])) return undefined;
  const [, id, what, key] = m;
  const none = id === mockRunIds.failed;
  if (method === "GET" && what === "results/accounts") return mockPage(none ? [] : MOCK_ACCOUNT_ROWS, path);
  if (method === "GET" && what === "results/communities") return mockPage(none ? [] : SEEDS.map((_, i) => mockCommunityRow(i)), path);
  if (method === "GET" && key !== undefined) {
    const i = none ? -1 : SEEDS.findIndex((s) => s.key === key);
    if (i < 0) throw new Error(`run ${id} has no community ${key}`);
    if (SEEDS[i].key === mockCommunityKeys.noEvidence) {
      throw new Error(`community ${key} has no evidence object yet; the summaries step (track 3b) writes it`);
    }
    return mockCommunityDetail(i);
  }
  if (method === "POST" && what === "dispositions") return mockDisposition(body);
  return undefined;
}
