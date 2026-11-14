// The mock answers the routes the real lab answers after wave 5: the source route, limit and offset on both results
// lists over more than one page, the community tag in score_parts, and a noisy-or policy with weights in [0, 1].
import { api, setMock } from "./client";
import { mockCommunityKeys, mockRunIds } from "./mock";

beforeEach(() => setMock(true));
afterEach(() => setMock(null));

const RUN = mockRunIds.done;

describe("the policy", () => {
  test("the account score is a noisy-or, with the three weight tiers factors.yaml ships, each in [0, 1]", async () => {
    const { models, policy } = await api.models();
    expect(policy.combine).toBe("noisy_or");
    expect(policy.alert_threshold).toBe(0.6);
    const weights = Object.fromEntries(models.filter((m) => m.kind === "factor").map((m) => [m.name, m.factor!.weight]));
    expect(weights).toEqual({
      shared_device_5plus: 0.2, new_accounts_shared_identifier: 0.65, in_and_out_within_a_day: 0.65,
      just_under_threshold: 0.65, fan_in_new_accounts: 0.2, velocity_anomaly: 0.05, tgn: 0.05,
    });
    for (const w of Object.values(weights)) { expect(w).toBeGreaterThanOrEqual(0); expect(w).toBeLessThanOrEqual(1); }
  });

  test("the community score is a noisy-or too, with its own weights in [0, 1]", async () => {
    const { policy } = await api.models();
    const community = policy.community as { combine: string; weights: Record<string, number> };
    expect(community.combine).toBe("noisy_or");
    expect(community.weights).toEqual({ member_risk: 0.65, shared_identifiers: 0.2, cycles: 0.2, new_accounts: 0.2, speed: 0.2 });
  });
});

describe("the source route", () => {
  test("a cypher factor answers Cypher, with its path, as the lab does", async () => {
    const file = await api.source("shared_device_5plus");
    expect(file).toMatchObject({ name: "shared_device_5plus", path: "factors/shared_device.cypher", language: "cypher" });
    expect(file.text).toContain("MATCH (a:Account)-[:USES_DEVICE]->(d:Device)");
    const other = await api.source("fan_in_new_accounts");
    expect(other).toMatchObject({ path: "factors/fan_in_new_accounts.cypher", language: "cypher" });
    expect(other.text).toContain("MATCH");
  });

  test("a model answers the Python of its example, and so does a python factor", async () => {
    expect(await api.source("tgn")).toMatchObject({ path: "examples/tgn/example.py", language: "python" });
    expect(await api.source("velocity_anomaly")).toMatchObject({ path: "lab/scoring/velocity.py", language: "python" });
    expect((await api.source("node2vec")).text).toContain("def run_model");
  });

  test("a name the lab does not know is refused, as the lab's 404 is", async () => {
    await expect(api.source("no_such_factor")).rejects.toThrow(/no model or factor named 'no_such_factor'/);
  });
});

describe("limit and offset on the results routes", () => {
  test("accounts: a total over one page, 100 rows by default, and the page asked for", async () => {
    const first = await api.accounts(RUN);
    expect(first.total).toBe(230);
    expect(first.rows).toHaveLength(100);
    expect(first.rows[0].key).toBe("A1001");
    const second = await api.accounts(RUN, { limit: 100, offset: 100 });
    expect(second.total).toBe(230);
    expect(second.rows).toHaveLength(100);
    expect(second.rows[0].key).toBe("A1101");
    const last = await api.accounts(RUN, { limit: 100, offset: 200 });
    expect(last.rows).toHaveLength(30);
    expect(last.rows.at(-1)!.key).toBe("A1230");
    expect((await api.accounts(RUN, { limit: 5, offset: 3 })).rows.map((r) => r.key)).toEqual(["A1004", "A1005", "A1006", "A1007", "A1008"]);
    expect((await api.accounts(RUN, { offset: 500 })).rows).toEqual([]); // past the end: empty, with the total still there
  });

  test("accounts come highest score first, whatever the page", async () => {
    const rows = [...(await api.accounts(RUN, { limit: 100 })).rows, ...(await api.accounts(RUN, { limit: 100, offset: 100 })).rows,
      ...(await api.accounts(RUN, { limit: 100, offset: 200 })).rows];
    for (let i = 1; i < rows.length; i++) expect(rows[i].risk_score).toBeLessThanOrEqual(rows[i - 1].risk_score);
    expect(new Set(rows.map((r) => r.key)).size).toBe(230);
  });

  test("communities: a total over one page too, best score first, and the page asked for", async () => {
    const first = await api.communities(RUN);
    expect(first.total).toBe(120);
    expect(first.rows).toHaveLength(100);
    expect(first.rows[0].key).toBe(mockCommunityKeys.ring);
    const second = await api.communities(RUN, { limit: 100, offset: 100 });
    expect(second.rows).toHaveLength(20);
    const all = [...first.rows, ...second.rows];
    for (let i = 1; i < all.length; i++) expect(all[i].score).toBeLessThanOrEqual(all[i - 1].score);
    expect(new Set(all.map((r) => r.key)).size).toBe(120);
  });

  test("limit and offset the lab would refuse are refused", async () => {
    await expect(api.accounts(RUN, { limit: 0 })).rejects.toThrow(/limit must be a whole number from 1 to 1000/);
    await expect(api.accounts(RUN, { limit: 1001 })).rejects.toThrow(/limit/);
    await expect(api.communities(RUN, { offset: -1 })).rejects.toThrow(/offset must be a whole number from 0/);
  });

  test("a run that stopped before scoring has an empty page and a total of 0", async () => {
    expect(await api.accounts(mockRunIds.failed, { limit: 100, offset: 100 })).toEqual({ total: 0, rows: [] });
    expect(await api.communities(mockRunIds.failed)).toEqual({ total: 0, rows: [] });
  });
});

describe("a community's score parts", () => {
  test("carry formed_by_identifier beside the five parts, and the score is the noisy-or of the five only", async () => {
    const { score_parts, row, evidence } = await api.community(RUN, mockCommunityKeys.ring);
    expect(Object.keys(score_parts)).toEqual(["member_risk", "shared_identifiers", "cycles", "new_accounts", "speed", "formed_by_identifier"]);
    expect(score_parts.formed_by_identifier).toBe(1);
    expect(evidence.score_parts).toEqual(score_parts);
    const w = { member_risk: 0.65, shared_identifiers: 0.2, cycles: 0.2, new_accounts: 0.2, speed: 0.2 };
    const noisy = 1 - Object.entries(w).reduce((t, [k, wi]) => t * (1 - wi * score_parts[k]), 1);
    expect(row.score).toBeCloseTo(noisy, 3);
  });

  test("a community that formed by payments says 0", async () => {
    expect((await api.community(RUN, "cf017a3d1e")).score_parts.formed_by_identifier).toBe(0);
  });
});
