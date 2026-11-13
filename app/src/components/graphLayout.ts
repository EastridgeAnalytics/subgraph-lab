// A force layout of our own for the small graphs on Results: Fruchterman-Reingold, started from a circle in the order
// the nodes came in, run a fixed number of rounds with no random numbers. The same graph always lands in the same
// place, so a reviewer who opens a community twice sees the same picture. Cost is about rounds x n^2; the round count
// drops as n grows so a few hundred nodes stays well under a second.
export interface Point { x: number; y: number }
export interface LayoutOptions { width?: number; height?: number; padding?: number; iterations?: number }

/** Where each node goes in a `width` x `height` box, inside `padding` of the edge. Edges to unknown nodes are ignored. */
export function layoutGraph(
  nodes: { id: string }[], edges: { source: string; target: string }[], opts: LayoutOptions = {},
): Map<string, Point> {
  const { width = 720, height = 420, padding = 28 } = opts;
  const n = nodes.length;
  const out = new Map<string, Point>();
  if (n === 0) return out;
  if (n === 1) { out.set(nodes[0].id, { x: width / 2, y: height / 2 }); return out; }

  const index = new Map(nodes.map((node, i) => [node.id, i] as const));
  const seen = new Set<number>();
  const links: [number, number][] = [];
  for (const e of edges) {
    const a = index.get(e.source), b = index.get(e.target);
    if (a === undefined || b === undefined || a === b) continue;
    const lo = Math.min(a, b), hi = Math.max(a, b);
    if (seen.has(lo * n + hi)) continue;
    seen.add(lo * n + hi);
    links.push([lo, hi]);
  }

  const cx = width / 2, cy = height / 2;
  const radius = Math.min(width, height) / 2 - padding;
  const x = new Float64Array(n), y = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    const angle = (2 * Math.PI * i) / n;
    x[i] = cx + radius * Math.cos(angle);
    y[i] = cy + radius * Math.sin(angle);
  }

  const rounds = Math.min(opts.iterations ?? 200, Math.max(20, Math.floor(2e7 / (n * n))));
  const k = 0.9 * Math.sqrt(((width - 2 * padding) * (height - 2 * padding)) / n);
  const start = Math.min(width, height) / 8;
  const dx = new Float64Array(n), dy = new Float64Array(n);

  const kk = k * k;
  for (let round = 0; round < rounds; round++) {
    dx.fill(0); dy.fill(0);
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let ex = x[i] - x[j], ey = y[i] - y[j];
        let d2 = ex * ex + ey * ey;
        if (d2 < 1e-4) { ex = 0.1 * (i + 1); ey = 0.1 * (j + 1); d2 = ex * ex + ey * ey; } // on top of each other: nudge apart
        const push = kk / d2; // repulsion k^2 / d along the unit vector, which is k^2 / d^2 times the offset
        dx[i] += ex * push; dy[i] += ey * push;
        dx[j] -= ex * push; dy[j] -= ey * push;
      }
    }
    for (const [a, b] of links) {
      const ex = x[a] - x[b], ey = y[a] - y[b];
      const pull = Math.max(Math.sqrt(ex * ex + ey * ey), 0.01) / k; // attraction d^2 / k along the unit vector, which is d / k times the offset
      dx[a] -= ex * pull; dy[a] -= ey * pull;
      dx[b] += ex * pull; dy[b] += ey * pull;
    }
    const temperature = start * (1 - round / rounds);
    for (let i = 0; i < n; i++) {
      dx[i] -= (x[i] - cx) * 0.1; dy[i] -= (y[i] - cy) * 0.1; // a light pull to the middle keeps loose pieces in view
      const len = Math.sqrt(dx[i] * dx[i] + dy[i] * dy[i]);
      if (len > 0) {
        const step = Math.min(len, temperature);
        x[i] = Math.min(width - padding, Math.max(padding, x[i] + (dx[i] / len) * step));
        y[i] = Math.min(height - padding, Math.max(padding, y[i] + (dy[i] / len) * step));
      }
    }
  }

  // Fit the result to the box without stretching it.
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (let i = 0; i < n; i++) {
    minX = Math.min(minX, x[i]); maxX = Math.max(maxX, x[i]);
    minY = Math.min(minY, y[i]); maxY = Math.max(maxY, y[i]);
  }
  const spanX = maxX - minX, spanY = maxY - minY;
  const scale = Math.min(spanX > 0 ? (width - 2 * padding) / spanX : Infinity, spanY > 0 ? (height - 2 * padding) / spanY : Infinity);
  const k2 = Number.isFinite(scale) ? scale : 1;
  nodes.forEach((node, i) => {
    out.set(node.id, { x: cx + (x[i] - (minX + maxX) / 2) * k2, y: cy + (y[i] - (minY + maxY) / 2) * k2 });
  });
  return out;
}
