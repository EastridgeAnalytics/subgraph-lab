// Results (pages 04, 05 and 06): what the run found, for a fraud reviewer to check. Two tabs, accounts and communities.
// A community opens to its members on a graph, its score and the parts of it, the summary with its source, the evidence,
// and the communities like it. Every account and community row carries the three decisions: confirmed, false positive,
// needs more. A decision goes to the lab with the reviewer's name and is staged, so it travels with the next push.
//
// Both lists page through the server, 100 rows a page, highest score first: `limit` and `offset` go to the lab, which
// answers `{total, rows}`. Previous and Next ask for the next page, and the range says "101 to 200 of 230". Sorting a
// column orders the rows of the page on screen, not the whole list. A community is addressed by `?community=<key>`
// beside `?run=`, so it can be linked to and the back button works. A community's score parts carry
// `formed_by_identifier` beside the five parts: ScoreParts shows it as a tag, not as a bar.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { AccountRow, CommunityRow, Disposition, DispositionRequest, ModelsList } from "../api/types";
import { CommunityGraph } from "../components/CommunityGraph";
import { DataTable, type DataColumn } from "../components/DataTable";
import { DispositionButtons, decisionLabel } from "../components/DispositionButtons";
import { EvidenceView } from "../components/EvidenceView";
import { errorMessage, formatInt } from "../components/format";
import { NoRunNotice, useRunRef } from "../components/RunRef";
import { formatScore, keyStart, shortKey, startOf } from "../components/score";
import { ScoreParts, type Combine } from "../components/ScoreParts";
import { SourceBadge, SummaryBlock } from "../components/SummaryBlock";
import { Button, Card, Field, Notice, Page, Stat, Tabs, type TabDef } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { useStoredText } from "../components/useStoredText";

const REVIEWER_KEY = "subgraph-lab.reviewer";
const PAGE = 100; // rows a page, asked of the lab as `limit`
// The community key goes into an API path, so one that is not a plain key is refused.
const COMMUNITY_KEY = /^[A-Za-z0-9][A-Za-z0-9_-]*$/;
const TABS: TabDef[] = [{ id: "accounts", label: "Accounts" }, { id: "communities", label: "Communities" }];
const linkClass = "font-medium text-indigo-700 underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600";

const plural = (n: number, one: string, many = `${one}s`) => `${formatInt(n)} ${n === 1 ? one : many}`;
const score = (n: number | null | undefined) => (n == null ? "none" : formatScore(n));

// ---- decisions ---------------------------------------------------------------------------------------------------------
type Kind = DispositionRequest["kind"];
interface Made { disposition: Disposition; by: string; staged: boolean }

/** Decisions made on this screen, by run, kind and key, with which are being saved and which failed. One at a time per row. */
function useDecisions(runId: string | null, reviewer: string) {
  const [made, setMade] = useState<Record<string, Made>>({});
  const [saving, setSaving] = useState<Record<string, true>>({});
  const [failed, setFailed] = useState<Record<string, string>>({});
  const inFlight = useRef(new Set<string>()); // set at once, so a double click cannot send two

  const at = (kind: Kind, key: string) => `${runId}|${kind}|${key}`;
  const drop = <T,>(from: Record<string, T>, k: string) => Object.fromEntries(Object.entries(from).filter(([x]) => x !== k));

  const decide = async (kind: Kind, key: string, disposition: Disposition) => {
    const by = reviewer.trim();
    const k = at(kind, key);
    if (!runId || !by || inFlight.current.has(k)) return;
    inFlight.current.add(k);
    setSaving((s) => ({ ...s, [k]: true }));
    setFailed((f) => drop(f, k));
    try {
      const result = await api.disposition(runId, { kind, key, disposition, by });
      if (result.ok) setMade((m) => ({ ...m, [k]: { disposition, by, staged: result.staged } }));
      else setFailed((f) => ({ ...f, [k]: "the lab did not accept the decision" }));
    } catch (e) {
      setFailed((f) => ({ ...f, [k]: errorMessage(e) }));
    } finally {
      inFlight.current.delete(k);
      setSaving((s) => drop(s, k));
    }
  };

  /** The three buttons for one row, showing the decision made here, else the one the lab already has. */
  const control = (kind: Kind, key: string, subject: string, loaded?: Disposition | null): ReactNode => {
    const k = at(kind, key);
    const m = made[k];
    return (
      <DispositionButtons subject={subject} value={m?.disposition ?? loaded ?? null} by={m?.by ?? null} staged={m ? m.staged : true}
        saving={Boolean(saving[k])} error={failed[k] ?? null} onChoose={(d) => decide(kind, key, d)}
        disabledReason={reviewer.trim() ? null : "Add your name above to record a decision."} />
    );
  };
  return control;
}

// ---- small pieces ------------------------------------------------------------------------------------------------------
function ScoreCell({ value, children }: { value: number; children?: ReactNode }) {
  return (
    <div className="min-w-28">
      <p className="font-semibold tabular-nums text-slate-900">{formatScore(value)}</p>
      <div aria-hidden="true" className="mt-1 h-1.5 w-full rounded-full bg-slate-100">
        <div className="h-full rounded-full bg-indigo-600" style={{ width: `${Math.round(Math.min(1, Math.max(0, value)) * 100)}%` }} />
      </div>
      {children}
    </div>
  );
}

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

/** The policy's community weights, from the models answer, or null when it did not come or has none. */
function communityWeights(models: ModelsList | null): Record<string, number> | null {
  const community = models && isObject(models.policy.community) ? models.policy.community : null;
  const w = community && isObject(community.weights) ? community.weights : null;
  if (!w) return null;
  const numbers = Object.entries(w).filter((e): e is [string, number] => typeof e[1] === "number");
  return numbers.length > 0 ? Object.fromEntries(numbers) : null;
}

/** How the policy combines a community's parts (`community.combine`, decision 45): "weighted_mean" when it says nothing,
 *  and null when the models answer did not come, so the screen claims nothing about it. */
function communityCombine(models: ModelsList | null): Combine | null {
  if (!models) return null;
  const community = isObject(models.policy.community) ? models.policy.community : null;
  return community?.combine === "noisy_or" ? "noisy_or" : "weighted_mean";
}

const PARTS_NOTE: Record<Combine, string> = {
  noisy_or: "Each part runs from 0 to 1. A part that is absent changes nothing; each part alone can raise the score up to its weight.",
  weighted_mean: "Each part runs from 0 to 1. The score is their weighted mean.",
};

function LoadProblem({ title, text, onRetry, extra }: { title: string; text: string; onRetry: () => void; extra?: ReactNode }) {
  return (
    <Notice kind="error" title={title}>
      <p>{text}</p>
      <div className="mt-3 flex flex-wrap gap-2"><Button onClick={onRetry}>Try again</Button>{extra}</div>
    </Notice>
  );
}

// ---- paging through the server -----------------------------------------------------------------------------------------
/** The offset of the page on screen. It goes back to the first page for another run. */
function usePageOffset(runId: string | null): [number, (offset: number) => void] {
  const [state, setState] = useState<{ run: string | null; at: number }>({ run: runId, at: 0 });
  return [state.run === runId ? state.at : 0, (at) => setState({ run: runId, at })];
}

/** The last answer for this scope, so the table stays on screen while the next page loads, not for another run's. */
function useKept<T>(data: T | null, scope: string): T | null {
  const last = useRef<{ scope: string; data: T } | null>(null);
  if (data !== null) last.current = { scope, data };
  return data ?? (last.current?.scope === scope ? last.current.data : null);
}

/** "Showing 101 to 200 of 230 accounts", with Previous and Next when there is more than one page. */
function ServerPager({ caption, noun, offset, shown, total, loading, onOffset }: {
  caption: string; noun: string; offset: number; shown: number; total: number; loading: boolean; onOffset: (offset: number) => void;
}) {
  if (total === 0) return null;
  const pages = Math.ceil(total / PAGE);
  const page = Math.floor(offset / PAGE) + 1;
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-slate-600">
      <p role="status">
        {loading ? `Loading page ${formatInt(page)}…`
          : shown === 0 ? `Nothing on this page: the list has ${formatInt(total)} ${noun}.`
          : `Showing ${formatInt(offset + 1)} to ${formatInt(offset + shown)} of ${formatInt(total)} ${noun}`}
      </p>
      {pages > 1 && (
        <nav aria-label={`Pages of ${caption}`} className="flex items-center gap-2">
          <Button onClick={() => onOffset(Math.max(0, offset - PAGE))} disabled={offset === 0 || loading}>Previous</Button>
          <span>Page {formatInt(page)} of {formatInt(pages)}</span>
          <Button onClick={() => onOffset(offset + PAGE)} disabled={offset + PAGE >= total || loading}>Next</Button>
        </nav>
      )}
    </div>
  );
}

// ---- one community -----------------------------------------------------------------------------------------------------
function CommunityView({ runId, communityKey, control, hrefFor, onBack, weights, combine }: {
  runId: string; communityKey: string; control: ReturnType<typeof useDecisions>; hrefFor: (key: string) => string;
  onBack: () => void; weights: Record<string, number> | null; combine: Combine | null;
}) {
  const { data, error, loading, reload } = useLoad(() => api.community(runId, communityKey), `community|${runId}|${communityKey}`);
  const heading = useRef<HTMLHeadingElement>(null);
  const loaded = data?.row.key;
  useEffect(() => { if (loaded) heading.current?.focus(); }, [loaded]); // a new community: say so to a screen reader

  const back = <div><Button onClick={onBack}>All communities</Button></div>;
  if (error) {
    return (
      <div className="space-y-4">
        {back}
        <LoadProblem title="This community did not load" text={error} onRetry={reload} />
      </div>
    );
  }
  if (!data || loading) return <div className="space-y-4">{back}<p role="status" className="text-sm text-slate-600">Loading the community…</p></div>;

  const { row, evidence } = data;
  const subject = `community ${keyStart(row.key)}`;
  return (
    <div className="space-y-6">
      {back}
      <div className="space-y-3">
        <h2 ref={heading} tabIndex={-1} className="text-xl font-semibold text-slate-900 focus:outline-none">
          Community <code className="break-all text-base">{row.key}</code>
        </h2>
        <section aria-label="Your decision on this community" className="rounded-lg border border-slate-200 bg-slate-50 p-4">
          <p className="mb-2 text-sm font-medium text-slate-800">Your decision on this community</p>
          {control("community", row.key, subject, row.disposition)}
        </section>
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
        <Stat label="Risk score" value={formatScore(row.score)} />
        <Stat label="Members" value={formatInt(row.size)} />
        <Stat label="Rank" value={`${formatInt(evidence.comparison.score_rank)} of ${formatInt(evidence.comparison.communities_in_slice)}`} hint="By score, in this slice" />
      </div>

      <Card title="Summary" description="Why this community could be risky, written from the evidence below and nothing else.">
        <SummaryBlock summary={data.summary} />
      </Card>

      <Card title="Score and its parts" description={combine ? PARTS_NOTE[combine] : "Each part runs from 0 to 1."}>
        <ScoreParts parts={data.score_parts} weights={weights} combine={combine} score={row.score} name="Parts of the community score" />
      </Card>

      <Card title="Members and links" description="The accounts in this community, the payments between them, and the identifiers they share.">
        <CommunityGraph key={row.key} nodes={data.nodes} edges={data.edges} memberCount={evidence.member_count} name={`Graph of community ${keyStart(row.key)}`} />
        <details className="rounded-md border border-slate-200">
          <summary className="cursor-pointer px-4 py-2 text-sm font-medium text-slate-800 focus-visible:outline-2 focus-visible:outline-indigo-600">
            All {formatInt(evidence.member_count)} members, as keys
          </summary>
          <p className="break-all border-t border-slate-200 px-4 py-3 text-sm"><code>{evidence.member_keys.join(", ")}</code></p>
        </details>
      </Card>

      <Card title="Evidence" description="What the summary was written from: keys and totals, never names.">
        <EvidenceView evidence={evidence} />
      </Card>

      <Card title="Communities like this one" description="One hybrid search over the summaries and the community embeddings.">
        {data.similar.length === 0 ? (
          <p className="text-sm text-slate-600">No similar communities came back. They need the summaries and embeddings indexed first.</p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {data.similar.map((s) => (
              <li key={s.key} className="space-y-1 py-3 first:pt-0 last:pb-0">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <Link to={hrefFor(s.key)} className={linkClass}><code>{shortKey(s.key, 12)}</code></Link>
                  <span className="text-sm text-slate-600">{plural(s.size, "member")}, risk score {formatScore(s.score)}</span>
                  {s.summary_source && <SourceBadge source={s.summary_source} />}
                  {s.disposition && <span className="text-sm font-medium text-slate-800">{decisionLabel(s.disposition)}</span>}
                </div>
                <p className="text-sm text-slate-600">{s.summary ? startOf(s.summary, 140) : "No summary."}</p>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

// ---- the screen --------------------------------------------------------------------------------------------------------
export default function Results() {
  const { runId, invalid, withRun } = useRunRef();
  const [params, setParams] = useSearchParams();
  const [reviewer, setReviewer] = useStoredText(REVIEWER_KEY);
  const openKey = params.get("community");
  const [tab, setTab] = useState(openKey ? "communities" : "accounts");
  useEffect(() => { if (openKey) setTab("communities"); }, [openKey]); // the back and forward buttons can change it

  const [accountsAt, setAccountsAt] = usePageOffset(runId);
  const [communitiesAt, setCommunitiesAt] = usePageOffset(runId);
  const accounts = useLoad(runId ? () => api.accounts(runId, { limit: PAGE, offset: accountsAt }) : null, `accounts|${runId}|${accountsAt}`);
  const communities = useLoad(runId ? () => api.communities(runId, { limit: PAGE, offset: communitiesAt }) : null, `communities|${runId}|${communitiesAt}`);
  const accountsShown = useKept(accounts.data, `accounts|${runId}`);
  const communitiesShown = useKept(communities.data, `communities|${runId}`);
  // Factor names and the policy's weights make the rows easier to read. Results works without them and says so.
  const models = useLoad(runId ? () => api.models() : null, "models");
  const control = useDecisions(runId, reviewer);

  // Back from a community puts focus on the list's first line, not nowhere. So does a new page, once it has loaded: the
  // button that was pressed is disabled while it loads.
  const listTop = useRef<HTMLParagraphElement>(null);
  const accountsTop = useRef<HTMLParagraphElement>(null);
  const wasOpen = useRef(false);
  const moved = useRef<"accounts" | "communities" | null>(null);
  useEffect(() => {
    if (wasOpen.current && !openKey) listTop.current?.focus();
    wasOpen.current = openKey !== null;
  }, [openKey]);
  useEffect(() => {
    if (moved.current === "accounts" && accounts.data) { accountsTop.current?.focus(); moved.current = null; }
  }, [accounts.data]);
  useEffect(() => {
    if (moved.current === "communities" && communities.data) { listTop.current?.focus(); moved.current = null; }
  }, [communities.data]);

  const purpose = "Check what the models found: accounts and communities, their scores and the reasons behind them. Mark each one confirmed, false positive or needs more.";
  if (!runId) return <Page title="Results" purpose={purpose}><NoRunNotice invalid={invalid} /></Page>;

  const hrefFor = (key: string) => { const p = new URLSearchParams(params); p.set("community", key); return `?${p.toString()}`; };
  const close = () => setParams((p) => { const next = new URLSearchParams(p); next.delete("community"); return next; });
  const names: Record<string, string> = Object.fromEntries(
    (models.data?.models ?? []).flatMap((m) => (m.kind === "factor" && m.factor ? [[m.factor.id, m.factor.name]] : [])));
  const weights = communityWeights(models.data);
  const combine = communityCombine(models.data);

  const accountColumns: DataColumn<AccountRow>[] = [
    { key: "key", header: "Account", sortValue: (r) => r.key, render: (r) => <code className="font-medium text-slate-900">{r.key}</code> },
    { key: "risk_score", header: "Risk score", firstSort: "desc", sortValue: (r) => r.risk_score,
      render: (r) => (
        <ScoreCell value={r.risk_score}>
          <p className="mt-1 text-xs text-slate-500">model {score(r.risk_score_model)}, rules {score(r.risk_score_rules)}</p>
        </ScoreCell>
      ) },
    { key: "factors", header: "Factors that fired", firstSort: "desc", sortValue: (r) => r.factors.length,
      render: (r) => r.factors.length === 0 ? <span className="text-slate-500">None fired</span> : (
        <ul className="space-y-0.5 text-xs">
          {r.factors.map((f) => (
            <li key={f.id} title={names[f.id]}>
              <code className="text-slate-900">{f.id}</code> <span className="text-slate-600">score {formatScore(f.score)}, weight {formatScore(f.weight)}</span>
            </li>
          ))}
        </ul>
      ) },
    { key: "community", header: "Community", sortValue: (r) => r.community_key ?? null,
      render: (r) => r.community_key
        ? <Link to={hrefFor(r.community_key)} onClick={() => setTab("communities")} title={r.community_key} aria-label={`Open community ${r.community_key}`} className={linkClass}><code>{shortKey(r.community_key, 10)}</code></Link>
        : <span className="text-slate-500">none</span> },
    { key: "alert", header: "Alert", sortValue: (r) => r.alert_key ?? null,
      render: (r) => (r.alert_key ? <code className="whitespace-nowrap text-xs">{r.alert_key}</code> : <span className="text-slate-500">no alert</span>) },
    { key: "disposition", header: "Decision", render: (r) => control("account", r.key, r.key, r.disposition) },
  ];

  const communityColumns: DataColumn<CommunityRow>[] = [
    { key: "key", header: "Community", sortValue: (r) => r.key,
      render: (r) => <Link to={hrefFor(r.key)} className={linkClass} title={r.key} aria-label={`Open community ${r.key}`}><code>{shortKey(r.key, 12)}</code></Link> },
    { key: "size", header: "Members", align: "right", firstSort: "desc", sortValue: (r) => r.size, render: (r) => formatInt(r.size) },
    { key: "score", header: "Risk score", firstSort: "desc", sortValue: (r) => r.score, render: (r) => <ScoreCell value={r.score} /> },
    { key: "summary", header: "Summary",
      render: (r) => r.summary ? (
        <div className="max-w-md space-y-1">
          <SourceBadge source={r.summary_source ?? "unknown"} />
          <p className="text-slate-700">{startOf(r.summary)}</p>
        </div>
      ) : <span className="text-slate-500">No summary</span> },
    { key: "disposition", header: "Decision", render: (r) => control("community", r.key, `community ${keyStart(r.key)}`, r.disposition) },
  ];

  const firstPage = (set: (offset: number) => void) => <Button onClick={() => set(0)}>Back to the first page</Button>;
  const goTo = (kind: "accounts" | "communities", set: (offset: number) => void) => (offset: number) => { moved.current = kind; set(offset); };

  return (
    <Page title="Results" purpose={purpose}>
      <Card title="Who is reviewing">
        <Field label="Your name" value={reviewer} onChange={(e) => setReviewer(e.target.value)} autoComplete="name"
          hint={reviewer.trim()
            ? "Recorded with every decision you make, and kept in this browser."
            : "Add your name to turn the decision buttons on. It is recorded with every decision you make, and kept in this browser."} />
        <p className="text-sm text-slate-600">
          A decision is staged and travels with the next push. Staging one after the review means opening{" "}
          <Link to={withRun("/review")} className={linkClass}>Review and push</Link> again before you push.
        </p>
      </Card>

      {models.error && (
        <Notice kind="warning" title="Factor names and policy weights did not load">
          {models.error}. The tables below still work, without factor names, and a community's score parts show without their weights.
        </Notice>
      )}

      <Tabs tabs={TABS} value={tab} label="Results" onChange={(t) => { setTab(t); if (t === "accounts" && openKey) close(); }}>
        <div hidden={tab !== "accounts"} className="space-y-4">
          {accounts.error ? <LoadProblem title="The accounts did not load" text={accounts.error} onRetry={accounts.reload}
              extra={accountsAt > 0 ? firstPage(setAccountsAt) : undefined} />
            : !accountsShown ? <p role="status" className="text-sm text-slate-600">Loading the accounts…</p>
            : (
              <>
                <p ref={accountsTop} tabIndex={-1} className="text-sm text-slate-600 focus:outline-none">
                  {plural(accountsShown.total, "account")} scored, highest risk score first. Click a column heading to sort the page you are on.
                </p>
                <DataTable columns={accountColumns} rows={accountsShown.rows} rowKey={(r) => r.key} caption="Accounts and their risk scores"
                  noun="accounts" pageSize={PAGE} footer={false} initialSort={{ key: "risk_score", dir: "desc" }}
                  empty="No accounts were scored in this run yet." />
                <ServerPager caption="Accounts and their risk scores" noun="accounts" offset={accountsAt} shown={accountsShown.rows.length}
                  total={accountsShown.total} loading={accounts.loading} onOffset={goTo("accounts", setAccountsAt)} />
              </>
            )}
        </div>

        <div hidden={tab !== "communities"} className="space-y-4">
          <div hidden={openKey !== null} className="space-y-4">
            {communities.error ? <LoadProblem title="The communities did not load" text={communities.error} onRetry={communities.reload}
                extra={communitiesAt > 0 ? firstPage(setCommunitiesAt) : undefined} />
              : !communitiesShown ? <p role="status" className="text-sm text-slate-600">Loading the communities…</p>
              : (
                <>
                  <p ref={listTop} tabIndex={-1} className="text-sm text-slate-600 focus:outline-none">
                    {plural(communitiesShown.total, "community", "communities")}, highest risk score first. Open one to see its members, the evidence and the summary.
                    Click a column heading to sort the page you are on.
                  </p>
                  <DataTable columns={communityColumns} rows={communitiesShown.rows} rowKey={(r) => r.key} caption="Communities and their risk scores"
                    noun="communities" pageSize={PAGE} footer={false} initialSort={{ key: "score", dir: "desc" }}
                    empty="No communities were found in this run yet." />
                  <ServerPager caption="Communities and their risk scores" noun="communities" offset={communitiesAt} shown={communitiesShown.rows.length}
                    total={communitiesShown.total} loading={communities.loading} onOffset={goTo("communities", setCommunitiesAt)} />
                </>
              )}
          </div>
          {openKey !== null && (COMMUNITY_KEY.test(openKey)
            ? <CommunityView runId={runId} communityKey={openKey} control={control} hrefFor={hrefFor} onBack={close} weights={weights} combine={combine} />
            : (
              <div className="space-y-4">
                <div><Button onClick={close}>All communities</Button></div>
                <Notice kind="warning" title="That is not a community key">The community in the address is not a key the lab makes. Pick one from the list.</Notice>
              </div>
            ))}
        </div>
      </Tabs>
    </Page>
  );
}
