// A community's members and the links between them, drawn as SVG with no library: accounts as circles (darker means a
// higher risk score), the identifiers two or more of them share as squares, payments as arrows from payer to payee,
// links to an identifier as dotted lines. The layout is ours (graphLayout.ts), so the picture is the same each time.
//
// It draws at most `cap` nodes (300 by default). Past that it keeps the highest-scoring accounts first, keeps room for
// the identifiers they share, and says how many it left out. Click a node, or pick a member in the list, to read what
// links to it: the list is the keyboard way in, since a few hundred tab stops on a picture would be none.
import { useId, useMemo, useState } from "react";
import type { GraphEdge, GraphNode } from "../api/types";
import { layoutGraph, type Point } from "./graphLayout";
import { formatAmount, formatScore } from "./score";
import { Field } from "./ui";

export const GRAPH_CAP = 300;
const W = 720;
const H = 420;

/** An identifier node has a `kind` (device, email, address...) in its props; an account does not. */
const isIdentifier = (n: GraphNode) => typeof n.props.kind === "string";
const scoreOf = (n: GraphNode): number | null => (typeof n.props.risk_score === "number" ? n.props.risk_score : null);

function pick(nodes: GraphNode[], cap: number): GraphNode[] {
  if (nodes.length <= cap) return nodes;
  const identifiers = nodes.filter(isIdentifier).slice(0, Math.floor(cap * 0.2));
  const accounts = nodes.filter((n) => !isIdentifier(n))
    .sort((a, b) => (scoreOf(b) ?? -1) - (scoreOf(a) ?? -1))
    .slice(0, cap - identifiers.length);
  return [...accounts, ...identifiers];
}

// From slate-300 to indigo-800 by score: one ramp, so shade alone carries the score and no cut-off is implied.
const FROM = [203, 213, 225];
const TO = [55, 48, 163];
function fill(score: number | null): string {
  if (score === null) return "#ffffff";
  const t = Math.min(1, Math.max(0, score));
  return `rgb(${FROM.map((c, i) => Math.round(c + (TO[i] - c) * t)).join(",")})`;
}

const label = (n: GraphNode) => (isIdentifier(n) ? `${String(n.props.kind)} ${String(n.props.key ?? n.id)}` : n.id);
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const pairKey = (from: string, to: string) => `${from}>${to}`;

export function CommunityGraph({ nodes, edges, cap = GRAPH_CAP, memberCount, name = "Community graph" }: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** The most nodes to draw. */
  cap?: number;
  /** How many members the community has, when the lab drew fewer of them. */
  memberCount?: number;
  /** Names the picture for screen readers. */
  name?: string;
}) {
  const markerId = `arrow-${useId().replace(/[^A-Za-z0-9]/g, "_")}`;
  const [selected, setSelected] = useState<string | null>(null);

  const shown = useMemo(() => pick(nodes, cap), [nodes, cap]);
  const byId = useMemo(() => new Map(shown.map((n) => [n.id, n] as const)), [shown]);
  const links = useMemo(() => edges.filter((e) => byId.has(e.source) && byId.has(e.target)), [edges, byId]);
  const points = useMemo(() => layoutGraph(shown, links, { width: W, height: H }), [shown, links]);

  if (nodes.length === 0) {
    return <p className="text-sm text-slate-600">The lab drew no members for this community.</p>;
  }

  const accountsDrawn = shown.filter((n) => !isIdentifier(n)).length;
  const identifiersDrawn = shown.length - accountsDrawn;
  const isPayment = (e: GraphEdge) => !isIdentifier(byId.get(e.target)!);
  const payments = links.filter(isPayment);
  const maxAmount = Math.max(1, ...payments.map((e) => (typeof e.props.amount === "number" ? e.props.amount : 0)));
  const paid = new Set(payments.map((e) => pairKey(e.source, e.target)));
  const r = shown.length <= 60 ? 9 : shown.length <= 150 ? 6 : 4;
  const withLabels = shown.length <= 40;
  const sel = selected ? byId.get(selected) ?? null : null;
  const left = nodes.length - shown.length;
  const unseen = memberCount !== undefined ? memberCount - accountsDrawn : 0;

  // A line from one node's edge to the other's, so an arrowhead is not buried under the circle it points at.
  const line = (e: GraphEdge, p: Point, q: Point, arrow: boolean) => {
    const dx = q.x - p.x, dy = q.y - p.y;
    const len = Math.hypot(dx, dy) || 1;
    const ux = dx / len, uy = dy / len;
    const side = paid.has(pairKey(e.target, e.source)) ? 3 : 0; // a payment each way: draw the two side by side
    const ox = -uy * side, oy = ux * side;
    const at = r + 1, end = arrow ? r + 4 : r + 1;
    return { x1: p.x + ux * at + ox, y1: p.y + uy * at + oy, x2: q.x - ux * end + ox, y2: q.y - uy * end + oy };
  };

  const incident = sel ? links.filter((e) => e.source === sel.id || e.target === sel.id) : [];
  const facts: string[] = [];
  if (sel) {
    if (isIdentifier(sel)) {
      const members = incident.map((e) => e.source);
      facts.push(`Shared by ${plural(members.length, "account")}: ${members.join(", ")}.`);
    } else {
      for (const e of incident) {
        const other = e.source === sel.id ? e.target : e.source;
        const o = byId.get(other)!;
        if (isIdentifier(o)) {
          const sharing = links.filter((x) => x.target === other).length - 1;
          facts.push(`Shares ${label(o)} with ${plural(sharing, "other account")}.`);
        } else {
          const n = typeof e.props.payments === "number" ? e.props.payments : null;
          const amount = typeof e.props.amount === "number" ? `, ${formatAmount(e.props.amount)} in all` : "";
          const count = n === null ? "Payments" : plural(n, "payment");
          facts.push(e.source === sel.id ? `${count} to ${other}${amount}.` : `${count} from ${other}${amount}.`);
        }
      }
    }
  }

  return (
    <figure className="space-y-3">
      <svg viewBox={`0 0 ${W} ${H}`} role="img"
        aria-label={`${name}: ${plural(accountsDrawn, "account")}, ${plural(identifiersDrawn, "shared identifier")} and ${plural(payments.length, "payment link")}`}
        className="h-auto w-full rounded-md border border-slate-200 bg-white">
        <defs>
          <marker id={markerId} viewBox="0 0 10 10" refX="9" refY="5" markerUnits="userSpaceOnUse" markerWidth="9" markerHeight="9" orient="auto-start-reverse">
            <path d="M0 0 L10 5 L0 10 z" fill="#64748b" />
          </marker>
        </defs>
        {links.map((e, i) => {
          const pay = isPayment(e);
          const amount = typeof e.props.amount === "number" ? e.props.amount : 0;
          const on = sel !== null && (e.source === sel.id || e.target === sel.id);
          return (
            <line key={`${e.source}>${e.target}>${i}`} {...line(e, points.get(e.source)!, points.get(e.target)!, pay)}
              stroke={on ? "#4f46e5" : "#94a3b8"} strokeWidth={pay ? 1 + 2.5 * (amount / maxAmount) : 1.25}
              strokeDasharray={pay ? undefined : "2 4"} strokeLinecap="round" markerEnd={pay ? `url(#${markerId})` : undefined} />
          );
        })}
        {shown.map((n) => {
          const p = points.get(n.id)!;
          const ident = isIdentifier(n);
          const on = sel?.id === n.id;
          const s = scoreOf(n);
          return (
            <g key={n.id} transform={`translate(${p.x} ${p.y})`} onClick={() => setSelected(on ? null : n.id)} className="cursor-pointer">
              <title>{ident ? label(n) : s === null ? n.id : `${n.id}, risk score ${formatScore(s)}`}</title>
              {ident
                ? <rect x={-r} y={-r} width={2 * r} height={2 * r} rx={2} fill="#ffffff" stroke={on ? "#4f46e5" : "#334155"} strokeWidth={on ? 3 : 1.75} />
                : <circle r={r} fill={fill(s)} stroke={on ? "#4f46e5" : "#334155"} strokeWidth={on ? 3 : 1} />}
              {withLabels && (
                <text y={r + 12} textAnchor="middle" fontSize="10" fill="#1e293b" stroke="#ffffff" strokeWidth="3" paintOrder="stroke">
                  {ident ? String(n.props.key ?? n.id) : n.id}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      <figcaption className="space-y-1 text-sm text-slate-600">
        <p>
          Circles are accounts, darker for a higher risk score. Squares are identifiers that members share. Arrows are
          payments, from payer to payee, thicker for more money. Dotted lines join an account to an identifier it shares.
        </p>
        {(left > 0 || unseen > 0) && (
          <p role="note" className="font-medium text-amber-900">
            {left > 0 && <>Drawing {shown.length} of the {nodes.length} nodes the lab sent, the highest-scoring accounts first. </>}
            {unseen > 0 && <>{accountsDrawn} of the community&apos;s {memberCount} members are drawn.</>}
          </p>
        )}
      </figcaption>
      <Field as="select" label="Member to inspect" value={sel ? sel.id : ""} onChange={(e) => setSelected(e.target.value || null)}
        hint="Pick a member, or click one on the picture, to read what links to it.">
        <option value="">None</option>
        {shown.map((n) => <option key={n.id} value={n.id}>{label(n)}</option>)}
      </Field>
      <div aria-live="polite" className="text-sm text-slate-700">
        {sel && (
          <div className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3">
            <p className="font-medium text-slate-900">
              {label(sel)}
              {!isIdentifier(sel) && scoreOf(sel) !== null && <span className="font-normal text-slate-600">, risk score {formatScore(scoreOf(sel)!)}</span>}
            </p>
            {facts.length > 0
              ? <ul className="mt-1 list-disc pl-5">{facts.map((f, i) => <li key={i}>{f}</li>)}</ul>
              : <p className="mt-1 text-slate-600">Nothing else in this picture links to it.</p>}
          </div>
        )}
      </div>
    </figure>
  );
}
