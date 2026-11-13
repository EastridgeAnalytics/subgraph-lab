// The evidence object behind a community's score and summary, laid out to be read (page 05): the identifiers members
// share, the money, the risk factors that fired, the top members, and how the community compares with the rest of the
// slice. It carries keys and totals, never names, and so does this view.
import type { ReactNode } from "react";
import type { Evidence } from "../api/types";
import { formatInt } from "./format";
import { formatAmount, formatHours, formatScore, formatWhen } from "./score";
import { Table, type Column } from "./ui";

type Shared = Evidence["shared_identifiers"][number];
type Fired = Evidence["factors"][number];
type Member = Evidence["top_members"][number];

const SHARED: Column<Shared>[] = [
  { key: "kind", header: "Kind" },
  { key: "key", header: "Identifier", render: (r) => <code>{r.key}</code> },
  { key: "members_sharing", header: "Members sharing", align: "right", render: (r) => formatInt(r.members_sharing) },
];
const FIRED: Column<Fired>[] = [
  { key: "name", header: "Risk factor", render: (r) => <>{r.name} <code className="text-xs text-slate-500">{r.factor_id}</code></> },
  { key: "accounts", header: "Accounts", align: "right", render: (r) => formatInt(r.accounts) },
  { key: "evidence_ids", header: "Evidence ids",
    render: (r) => (r.evidence_ids.length === 0 ? "none" : (
      <span className="break-all text-xs">
        {r.evidence_ids.slice(0, 3).join(", ")}{r.evidence_ids.length > 3 ? ` and ${r.evidence_ids.length - 3} more` : ""}
      </span>)) },
];
const TOP: Column<Member>[] = [
  { key: "key", header: "Account", render: (r) => <code>{r.key}</code> },
  { key: "score", header: "Risk score", align: "right", render: (r) => formatScore(r.score) },
];

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
      {children}
    </div>
  );
}

export function EvidenceView({ evidence: e }: { evidence: Evidence }) {
  const m = e.money;
  const facts: [string, string][] = [
    ["Money in", formatAmount(m.inflow)],
    ["Money out", formatAmount(m.outflow)],
    ["Paid between members", formatAmount(m.internal)],
    ["Cycles found", formatInt(m.cycles)],
    ["First payment", formatWhen(m.first_at)],
    ["Last payment", formatWhen(m.last_at)],
    ["Time span", m.span_hours == null ? "unknown" : formatHours(m.span_hours)],
  ];
  const c = e.comparison;
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <Section title="Identifiers members share">
        <Table columns={SHARED} rows={e.shared_identifiers} rowKey={(r) => `${r.kind}:${r.key}`} caption="Identifiers members share"
          empty="No identifier is shared by two or more members." />
      </Section>
      <Section title="Money">
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 rounded-md border border-slate-200 p-4 text-sm">
          {facts.map(([k, v]) => (
            <div key={k}>
              <dt className="text-slate-600">{k}</dt>
              <dd className="font-medium text-slate-900">{v}</dd>
            </div>
          ))}
        </dl>
      </Section>
      <Section title="Risk factors that fired">
        <Table columns={FIRED} rows={e.factors} rowKey={(r) => r.factor_id} caption="Risk factors that fired in this community"
          empty="No risk factor fired for these members." />
      </Section>
      <Section title="Top members by score">
        <Table columns={TOP} rows={e.top_members} rowKey={(r) => r.key} caption="Top members by risk score" empty="No members to list." />
      </Section>
      <div className="lg:col-span-2">
        <Section title="Against the rest of the slice">
          <p className="text-sm text-slate-700">
            Ranks {formatInt(c.score_rank)} of {formatInt(c.communities_in_slice)} communities by score. The median community has{" "}
            {formatAmount(c.median_size)} members and a score of {formatScore(c.median_score)}.
          </p>
        </Section>
      </div>
    </div>
  );
}
