/* subgraph-lab in plain English: the four figures on about.html, drawn in Eastridge Analytics' technical-figures
   house style (isometric line drawings in one blue, Departure Mono labels, EB Garamond text, click to open).
   Data first (parts, connections, paths), drawing second, so the card and the drawing say the same thing.
   Inlined into about.html by demo/build_about.py. Edit this file and demo/about/about.md, then rebuild. */

const NS = 'http://www.w3.org/2000/svg', C = 0.8660254, S = 0.5;
const COL = { line:'#2341ff', mute:'#7a7f99', red:'#e5412d',
  ours:{ top:'#f4f5fb', side:'#e7eafc', front:'#dce1fb' }, hot:{ top:'#6a7cf6', side:'#5468f2', front:'#4a5ef0' },
  out:{ top:'#ffffff', side:'#f8f9fe', front:'#f1f3fd' }, shell:{ top:'#eef1fd', side:'#e3e8fc', front:'#d8defb' } };
const pts = (...a) => a.map(p => p.map(v => v.toFixed(1)).join(',')).join(' ');
const el = (tag, attrs, parent) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); parent && parent.appendChild(e); return e; };
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;');
const FIGS = [];

function Figure(spec) {
  const K = { s:spec.s || 15, cx:spec.w / 2, cy:spec.h / 2 };
  const P = (x, y, z = 0) => [K.cx + (x - y) * C * K.s, K.cy + (x + y) * S * K.s - z * K.s];
  const Q = (sx, sy) => { const u = (sx - K.cx) / (C * K.s), v = (sy - K.cy) / (S * K.s); return [(u + v) / 2, (v - u) / 2]; };
  const dashA = d => d ? { 'stroke-dasharray':'4 3' } : {};
  const poly = (g, p, fill, d, extra = {}) => el('polygon', { points:pts(...p), fill, stroke:COL.line, 'stroke-width':1, 'stroke-linejoin':'round', ...dashA(d), ...extra }, g);
  function box(g, x, y, z, w, d, h, c, dashed, extra) {
    poly(g, [P(x+w,y,z), P(x+w,y+d,z), P(x+w,y+d,z+h), P(x+w,y,z+h)], c.side, dashed, extra);
    poly(g, [P(x,y+d,z), P(x+w,y+d,z), P(x+w,y+d,z+h), P(x,y+d,z+h)], c.front, dashed, extra);
    return poly(g, [P(x,y,z+h), P(x+w,y,z+h), P(x+w,y+d,z+h), P(x,y+d,z+h)], c.top, dashed, extra);
  }
  function cyl(g, x, y, z, w, d, h, c, dashed) {
    const r = Math.min(w, d) / 2, ox = x + w / 2, oy = y + d / 2, rx = r * K.s * 1.2247, ry = r * K.s * .7071;
    const [bx, by] = P(ox, oy, z), [tx, ty] = P(ox, oy, z + h);
    el('path', { d:`M${bx-rx},${ty} L${bx-rx},${by} A${rx},${ry} 0 0 0 ${bx+rx},${by} L${bx+rx},${ty} Z`, fill:c.side, stroke:COL.line, 'stroke-width':1, ...dashA(dashed) }, g);
    el('ellipse', { cx:tx, cy:ty, rx, ry, fill:c.top, stroke:COL.line, 'stroke-width':1, ...dashA(dashed) }, g);
    return { tx, ty, rx, ry, bx, by };
  }
  const seg = (g, a, b, extra = {}) => el('line', { x1:a[0], y1:a[1], x2:b[0], y2:b[1], stroke:COL.line, 'stroke-width':.8, ...extra }, g);
  function text(g, x, y, s, o = {}) { const t = el('text', { x, y, 'font-size':o.size || 11, fill:o.fill || COL.line, 'text-anchor':o.anchor || 'middle' }, g); t.textContent = s; return t; }
  const F = o => o.hot ? COL.hot : o.out ? COL.out : COL.ours;

  /* ---------- Each part drawn as what it does ---------- */
  const D = {
    sheet: (g, x, y, o) => {                                  /* a sheet of rows: a file, a list, a pack */
      box(g, x - 1.25, y - 1.45, 0, 2.5, 2.9, .12, COL.out, o.dashed);
      box(g, x - 1.1, y - 1.6, .12, 2.5, 2.9, .14, F(o), o.dashed);
      const z = .26, ink = o.hot ? '#ffffff' : COL.line;
      for (let r = 1; r < 5; r++) seg(g, P(x - 1.1, y - 1.6 + r * .58, z), P(x + 1.4, y - 1.6 + r * .58, z), { stroke:ink, opacity:.65 });
      seg(g, P(x - .4, y - 1.6, z), P(x - .4, y + 1.3, z), { stroke:ink, opacity:.65 });
    },
    stack: (g, x, y, o) => {                                  /* stacked pages: summaries, documents */
      for (let k = 0; k < 3; k++) box(g, x - 1.1 + k * .12, y - 1.3 - k * .12, k * .22, 2.2, 2.6, .14, k === 2 ? F(o) : COL.out, o.dashed);
      for (let r = 0; r < 3; r++) seg(g, P(x - .6, y - .9 + r * .5, .72), P(x + .7, y - .9 + r * .5, .72), { opacity:.5 });
    },
    db: (g, x, y, o) => {                                     /* a database: a cylinder with two rings */
      const h = o.h || 1.9, c = cyl(g, x - 1.2, y - 1.2, 0, 2.4, 2.4, h, F(o), o.dashed);
      [h * .34, h * .66].forEach(zz => { const [bx, by] = P(x, y, zz); el('path', { d:`M${bx - c.rx},${by} A${c.rx},${c.ry} 0 0 0 ${bx + c.rx},${by}`, fill:'none', stroke:COL.line, 'stroke-width':.8, opacity:.55, ...dashA(o.dashed) }, g); });
      return c;
    },
    graph: (g, x, y, o) => {                                  /* a graph database: a cylinder with nodes on top */
      const c = D.db(g, x, y, { ...o, h:1.5 });
      const n = [[-.5, -.1], [.25, -.45], [.05, .35], [.55, .2]].map(([dx, dy]) => [c.tx + dx * c.rx, c.ty + dy * c.ry]);
      [[0, 1], [0, 2], [1, 3], [2, 3], [1, 2]].forEach(([a, b]) => seg(g, n[a], n[b], { opacity:.85, ...(o.dashed ? { 'stroke-dasharray':'2 2' } : {}) }));
      n.forEach(p => el('circle', { cx:p[0], cy:p[1], r:2.6, fill:o.hot ? '#ffffff' : COL.hot.top, stroke:COL.line, 'stroke-width':.8 }, g));
    },
    door: (g, x, y, o) => {                                   /* an intake: a doorway with a sheet passing through */
      box(g, x - .45, y - 1.9, 0, .9, .6, 2.6, F(o), o.dashed);
      box(g, x - 1.7, y - .7, .9, 1.1, 1.4, .08, COL.ours);
      box(g, x - .45, y + 1.3, 0, .9, .6, 2.6, F(o), o.dashed);
      box(g, x - .45, y - 1.9, 2.6, .9, 3.8, .45, F(o), o.dashed);
    },
    filter: (g, x, y, o) => {                                 /* a sieve: a plate with slits, for finding */
      box(g, x - 1.3, y - 1.3, 0, 2.6, 2.6, .15, COL.out, true);
      box(g, x - .2, y - 1.5, .15, .4, 3.0, 2.0, F(o), o.dashed);
      for (let i = 0; i < 5; i++) { const yy = y - 1.2 + i * .6; seg(g, P(x + .2, yy, .45), P(x + .2, yy, 1.85), { opacity:.7 }); }
    },
    fork: (g, x, y, o) => {                                   /* a decision: a block with a branching track */
      box(g, x - 1.1, y - 1.1, 0, 2.2, 2.2, .55, F(o), o.dashed);
      const z = .56, a = P(x - .9, y, z), m = P(x, y, z);
      [[a, m], [m, P(x + .9, y - .7, z)], [m, P(x + .9, y + .7, z)]].forEach(([p, q]) => seg(g, p, q, { 'stroke-width':1.4 }));
    },
    cage: (g, x, y, o) => {                                   /* an AI model: a figure at a console in a dashed cage */
      box(g, x - 1.3, y - 1.3, 0, 2.6, 2.6, .15, COL.ours);
      box(g, x - 1.0, y - 1.0, .15, .9, 2.0, .8, COL.ours);
      box(g, x - 1.0, y - .9, .95, .12, 1.8, .7, o.hot ? COL.hot : COL.ours);
      cyl(g, x + .25, y - .3, .15, .6, .6, .85, COL.hot); cyl(g, x + .35, y - .2, 1.0, .4, .4, .35, COL.hot);
      const W = (a, b) => seg(g, a, b, { 'stroke-dasharray':'3 3', 'stroke-width':.9 });
      const X0 = x - 1.3, X1 = x + 1.3, Y0 = y - 1.3, Y1 = y + 1.3, H = 2.5;
      [[X1, Y0], [X1, Y1], [X0, Y1]].forEach(([a, b]) => W(P(a, b, .15), P(a, b, H)));
      [[X0, Y0, X1, Y0], [X1, Y0, X1, Y1], [X1, Y1, X0, Y1], [X0, Y1, X0, Y0]].forEach(([a, b, c2, d2]) => W(P(a, b, H), P(c2, d2, H)));
    },
    desk: (g, x, y, o) => {                                   /* a person at a desk, reviewing */
      box(g, x - 1.5, y - 1.3, 0, 3, 2.6, .25, COL.out, true);
      box(g, x - 1.1, y - 1.0, .25, 2.2, .16, 1.3, F(o)); box(g, x - 1.2, y - .8, .25, 2.4, 1.3, .1, COL.ours);
      box(g, x - .3, y + .7, .25, .7, .7, .7, COL.ours);
    },
    people: (g, x, y, o) => {
      box(g, x - 1.8, y - 1.5, 0, 3.6, 3, .2, COL.out, true);
      [[-.9, -.6], [.5, -.7], [-.2, .6]].forEach(([dx, dy], i) => { const c = i === 2 && o.hot ? COL.hot : COL.ours;
        cyl(g, x + dx - .35, y + dy - .35, .2, .7, .7, 1.0, c, o.dashed); cyl(g, x + dx - .25, y + dy - .25, 1.2, .5, .5, .4, c, o.dashed); });
    },
    terminal: (g, x, y, o) => {                               /* a laptop: an app someone works in */
      box(g, x - 1.5, y - 1.2, 0, 3, 2.4, .2, F(o), o.dashed);
      box(g, x - 1.5, y - 1.2, .2, 3, .16, 1.7, F(o), o.dashed);
      for (let r = 0; r < 3; r++) seg(g, P(x - 1.2 + r * .2, y - 1.04, 1.5 - r * .35), P(x + .6 - r * .5, y - 1.04, 1.5 - r * .35), { opacity:.55 });
    },
    mail: (g, x, y, o) => { box(g, x - 1.3, y - 1, 0, 2.6, 2, 1, F(o), o.dashed); const m = P(x, y, 1); seg(g, P(x - 1.3, y - 1, 1), m); seg(g, P(x - 1.3, y + 1, 1), m); },
    chat: (g, x, y, o) => {                                   /* a chat window: bubbles on a pane */
      box(g, x - 1.4, y - 1.2, 0, 2.8, 2.4, 1.6, F(o), o.dashed);
      const [a, b] = P(x - .6, y + 1.2, 1.15), [c, d] = P(x + .5, y + 1.2, .6);
      el('rect', { x:a - 10, y:b - 6, width:24, height:10, rx:4, fill:'#fff', stroke:COL.line, 'stroke-width':.8, ...dashA(o.dashed) }, g);
      el('rect', { x:c - 6, y:d - 6, width:20, height:10, rx:4, fill:o.hot ? COL.hot.top : '#c9d0fa', stroke:COL.line, 'stroke-width':.8, ...dashA(o.dashed) }, g);
    },
    trend: (g, x, y, o) => {                                  /* outside data: a panel with two lines, one lagging the other */
      box(g, x - 1.5, y - .9, 0, 3, 1.8, .15, COL.out, true);
      box(g, x - 1.3, y - .12, .15, 2.6, .24, 2.0, COL.out, o.dashed);
      const line = (v, ex) => el('polyline', { points:pts(...v.map((z, i) => P(x - 1.1 + i * .44, y + .12, .3 + z))), fill:'none', stroke:COL.line, 'stroke-width':1, ...ex }, g);
      line([.4, .8, .6, 1.2, 1.0, 1.5], {}); line([.3, .35, .7, .55, 1.1, .9], { 'stroke-dasharray':'2 2', opacity:.7 });
    },
    rack: (g, x, y, o) => {                                   /* a platform: a server with its slats */
      box(g, x - 1.4, y - 1.4, 0, 2.8, 2.8, 3.4, F(o), o.dashed);
      for (let z = .6; z < 3.2; z += .55) seg(g, P(x - 1.2, y + 1.4, z), P(x + 1.2, y + 1.4, z), { opacity:.45 });
    },
    machine: (g, x, y, o) => {                                /* a machine: models at work */
      box(g, x - 1.4, y - 1.1, 0, 2.8, 2.2, 1.1, F(o), o.dashed);
      box(g, x - .9, y - .7, 1.1, 1.6, 1.4, .45, COL.ours);
      box(g, x - .7, y - .55, 1.55, 1.2, 1.05, .1, COL.out);
      seg(g, P(x - .8, y + 1.1, .55), P(x + .8, y + 1.1, .55), { 'stroke-width':1.4 });
    },
    dial: (g, x, y, o) => {                                   /* a scheduler: a dial */
      const c = cyl(g, x - 1, y - 1, 0, 2, 2, .45, F(o), o.dashed);
      for (let i = 0; i < 8; i++) { const a = i / 8 * 2 * Math.PI; seg(g, [c.tx + Math.cos(a) * c.rx * .74, c.ty + Math.sin(a) * c.ry * .74], [c.tx + Math.cos(a) * c.rx * .9, c.ty + Math.sin(a) * c.ry * .9], { opacity:.7 }); }
      seg(g, [c.tx, c.ty], [c.tx + c.rx * .55, c.ty - c.ry * .45], { 'stroke-width':1.6 });
      el('circle', { cx:c.tx, cy:c.ty, r:2, fill:COL.line }, g);
    },
    account: (g, x, y, o) => {                                /* a bank account: a card with a stripe and a chip */
      box(g, x - 1.2, y - .8, 0, 2.4, 1.6, .2, F(o), o.dashed);
      const z = .2, ink = o.hot ? '#ffffff' : COL.line;
      seg(g, P(x - 1.2, y - .35, z), P(x + 1.2, y - .35, z), { stroke:ink, 'stroke-width':1.3, opacity:.8 });
      box(g, x - .85, y + .05, z, .5, .4, .06, o.hot ? COL.ours : COL.hot);
    },
    house: (g, x, y, o) => {                                  /* an address: a house with a pitched roof and a door */
      const w = 1.2, h = 1.4, r = 2.4, c = F(o), dash = o.dashed;
      box(g, x - w, y - w, 0, 2 * w, 2 * w, h, c, dash);
      poly(g, [P(x - w, y - w, h), P(x, y - w, r), P(x, y + w, r), P(x - w, y + w, h)], c.top, dash);
      poly(g, [P(x, y - w, r), P(x + w, y - w, h), P(x + w, y + w, h), P(x, y + w, r)], c.side, dash);
      poly(g, [P(x - w, y + w, h), P(x + w, y + w, h), P(x, y + w, r)], c.front, dash);
      poly(g, [P(x - .2, y + w, 0), P(x + .35, y + w, 0), P(x + .35, y + w, .85), P(x - .2, y + w, .85)], o.hot ? COL.ours.top : COL.hot.top);
    },
    step: (g, x, y, o) => { box(g, x - 1.8, y - 1.8, 0, 3.6, 3.6, o.stepH, COL.ours); },
  };

  /* ---------- Frame ---------- */
  const uid = spec.svg, svg = document.getElementById(uid);
  svg.setAttribute('viewBox', `0 0 ${spec.w} ${spec.h}`);
  svg.setAttribute('aria-label', spec.aria);
  const card = document.getElementById(spec.card), ctl = document.getElementById(spec.ctl);
  const defs = el('defs', {}, svg);
  const pat = el('pattern', { id:'dots-' + uid, width:8, height:8, patternUnits:'userSpaceOnUse' }, defs); el('circle', { cx:1, cy:1, r:.7, fill:'#c9cde6' }, pat);
  [['ar-' + uid, COL.line], ['ar-red-' + uid, COL.red]].forEach(([id, f]) => { const m = el('marker', { id, viewBox:'0 0 10 10', refX:9, refY:5, markerWidth:6, markerHeight:6, orient:'auto-start-reverse' }, defs); el('path', { d:'M0,1 L9,5 L0,9 z', fill:f }, m); });
  el('rect', { x:20, y:20, width:spec.w - 40, height:spec.h - 40, fill:`url(#dots-${uid})`, stroke:'#c9cde6', 'stroke-dasharray':'1 3' }, svg);
  const vt = (x, y, s) => { const t = el('text', { x, y, 'font-size':11, fill:COL.mute, transform:`rotate(90 ${x} ${y})` }, svg); t.textContent = s; };
  vt(36, 34, spec.num); vt(spec.w - 36, 34, `[ ${spec.title} ]`); vt(spec.w - 36, spec.h - 96, '(C) 2026');
  (spec.zones || []).forEach(z => { el('rect', { x:z.x, y:z.y, width:z.w, height:z.h, fill:'none', stroke:'#c9cde6', 'stroke-dasharray':'6 4' }, svg);
    text(svg, z.x + 10, z.y + 16, z.label, { size:10, fill:COL.mute, anchor:'start' }); });
  (spec.notes || []).forEach(n => text(svg, n.x, n.y, n.text, { size:9.5, fill:COL.mute, anchor:n.anchor || 'start' }));

  const scene = el('g', {}, svg);
  const L = { conns:el('g', {}, scene), back:el('g', {}, scene), front:el('g', {}, scene), labels:el('g', {}, scene) };
  const OBJ = {};
  spec.parts.forEach(o => {
    const g = el('g', { class:'obj', 'data-id':o.id, tabindex:0, role:'button', 'aria-label':o.name }, L[o.layer || 'front']);
    OBJ[o.id] = { g, ...o };
    if (o.stepH) { D.step(g, ...Q(o.sx, o.sy), o); const [x, y] = Q(o.sx, o.sy - o.stepH * K.s); if (o.on) D[o.on](g, x, y, o.onOpts || {}); }
    else { const [x, y] = Q(o.sx, o.sy); D[o.draw](g, x, y, o); }
  });

  /* ---------- Connections: ports spread along each side, ordered by where the other end is ---------- */
  const BB = id => { const b = OBJ[id].g.getBBox(); return { x:b.x, y:b.y, w:b.width, h:b.height, cx:b.x + b.width / 2, cy:b.y + b.height / 2 }; };
  const CONN = spec.conns;
  const ends = {};
  CONN.forEach(c => { [[c.a, c.f, c.b], [c.b, c.t, c.a]].forEach(([id, s, other]) => { (ends[id + s] = ends[id + s] || []).push({ c, other }); }); });
  Object.entries(ends).forEach(([k, list]) => { const s = k.slice(-1); list.sort((p, q) => (s === 'l' || s === 'r') ? BB(p.other).cy - BB(q.other).cy : BB(p.other).cx - BB(q.other).cx); });
  const port = (id, s, c, frac) => {
    const b = BB(id), list = ends[id + s], i = list.findIndex(e => e.c === c), n = list.length, k = frac ?? (i + 1) / (n + 1), pad = 6;
    if (s === 'r') return [b.x + b.w + pad, b.y + b.h * (.25 + .5 * k)];
    if (s === 'l') return [b.x - pad, b.y + b.h * (.25 + .5 * k)];
    if (s === 't') return [b.x + b.w * (.3 + .4 * k), b.y - pad];
    return [b.x + b.w * (.3 + .4 * k), b.y + b.h + pad];
  };
  CONN.forEach(c => {
    const A = port(c.a, c.f, c, c.fk), B = port(c.b, c.t, c, c.tk), hz = s => s === 'l' || s === 'r';
    let r;
    if (c.lane !== undefined) r = [A, [A[0], c.lane], [B[0], c.lane], B];
    else if (hz(c.f) && hz(c.t)) { const mx = A[0] + (B[0] - A[0]) * (c.mx ?? .5); r = Math.abs(A[1] - B[1]) < 2 ? [A, B] : [A, [mx, A[1]], [mx, B[1]], B]; }
    else if (!hz(c.f) && !hz(c.t)) { const my = A[1] + (B[1] - A[1]) * (c.my ?? .5); r = Math.abs(A[0] - B[0]) < 2 ? [A, B] : [A, [A[0], my], [B[0], my], B]; }
    else if (hz(c.f)) r = [A, [B[0], A[1]], B];
    else r = [A, [A[0], B[1]], B];
    c.r = r;
    c.el = el('polyline', { class:'conn' + (c.cls ? ' ' + c.cls : ''), points:pts(...r), 'marker-end':`url(#${c.cls === 'red' ? 'ar-red-' : 'ar-'}${uid})` }, L.conns);
  });

  /* ---------- Labels ---------- */
  function label(id, lines, pos) {
    if (pos === 'none') { OBJ[id].lab = null; return; }
    const g = el('g', { class:'clabel', 'data-for':id }, L.labels), b = BB(id);
    const sty = i => ({ size:i ? 10 : 11, fill:i ? COL.mute : COL.line });
    if (pos === 'right') lines.forEach((s, i) => text(g, b.x + b.w + 12, b.cy - 2 + i * 13, s, { ...sty(i), anchor:'start' }));
    else if (pos === 'left') lines.forEach((s, i) => text(g, b.x - 12, b.cy - 2 + i * 13, s, { ...sty(i), anchor:'end' }));
    else if (pos === 'above') { const y0 = b.y - 10 - (lines.length - 1) * 13; lines.forEach((s, i) => text(g, b.cx, y0 + i * 13, s, sty(i))); }
    else { const y0 = b.y + b.h + 18; lines.forEach((s, i) => text(g, b.cx, y0 + i * 13, s, sty(i))); }
    OBJ[id].lab = g;
  }
  spec.parts.forEach(o => label(o.id, o.lab, o.pos));

  function placeLineLabels() {
    const hit = (a, b) => a.x < b.x + b.width + 4 && b.x < a.x + a.width + 4 && a.y < b.y + b.height + 2 && b.y < a.y + a.height + 2;
    const taken = [...L.labels.querySelectorAll('text')].map(t => t.getBBox());
    Object.values(OBJ).forEach(o => taken.push(o.g.getBBox()));
    CONN.filter(c => c.label).forEach(c => {
      const g = el('g', { class:'clabel line-lbl' + (c.cls === 'red' ? ' red' : '') }, L.labels), t = text(g, 0, 0, c.label, { size:9.5, fill:c.cls === 'red' ? COL.red : COL.line });
      const segs = c.r.slice(1).map((b, i) => [c.r[i], b]).sort((p, q) => Math.hypot(q[1][0] - q[0][0], q[1][1] - q[0][1]) - Math.hypot(p[1][0] - p[0][0], p[1][1] - p[0][1]));
      let best = null;
      for (const [a, b] of segs) { for (const f of [.5, .35, .65, .22, .78, .12, .88]) {
        const x = a[0] + (b[0] - a[0]) * f, y = a[1] + (b[1] - a[1]) * f; t.setAttribute('x', x); t.setAttribute('y', y + 3.5);
        const bb = t.getBBox(); if (bb.x > 24 && bb.x + bb.width < spec.w - 24 && !taken.some(o => hit(bb, o))) { best = bb; break; } } if (best) break; }
      if (!best) { const [a, b] = segs[0]; t.setAttribute('x', (a[0] + b[0]) / 2); t.setAttribute('y', (a[1] + b[1]) / 2 + 3.5); best = t.getBBox(); }
      g.insertBefore(el('rect', { x:best.x - 3, y:best.y - 1, width:best.width + 6, height:best.height + 2, fill:'#fff' }), t);
      taken.push(best); c.lab = g;
    });
  }

  /* ---------- The card ---------- */
  const tag = s => s ? `<span class="tag ${s[0]}">${esc(s[1])}</span>` : '';
  function overview() {
    const ov = spec.overview;
    card.innerHTML = `<h3>${esc(ov.title)}</h3><p class="where">${esc(ov.where)}</p>${ov.d.map(t => `<p>${t}</p>`).join('')}<ul>${
      ov.order.map(id => `<li data-id="${id}">${esc(OBJ[id].name)}</li>`).join('')}</ul>`;
    card.querySelectorAll('li').forEach(li => li.onclick = () => select(li.dataset.id));
  }
  function show(id) {
    const o = OBJ[id], links = [...new Set(CONN.filter(c => c.a === id || c.b === id).map(c => c.a === id ? c.b : c.a))];
    card.innerHTML = `<h3>${esc(o.name)} ${tag(o.status)}</h3><p class="where">${esc(o.where)}</p>${o.d.map(t => `<p>${t}</p>`).join('')}${
      o.files ? `<p class="files">${esc(o.files)}</p>` : ''}${
      links.length ? `<p class="where" style="margin-top:12px">Talks to</p><ul>${links.map(l => `<li data-id="${l}">${esc(OBJ[l].name)}</li>`).join('')}</ul>` : ''}`;
    card.querySelectorAll('li').forEach(li => li.onclick = () => select(li.dataset.id));
  }

  /* ---------- Interaction ---------- */
  let sel = null, route = null;
  const clearBtn = document.createElement('button'); clearBtn.type = 'button'; clearBtn.textContent = 'Show everything'; clearBtn.hidden = true;
  Object.entries(spec.routes || {}).forEach(([k, R]) => { const b = document.createElement('button'); b.type = 'button'; b.dataset.route = k; b.textContent = R.name; b.onclick = () => follow(k); ctl.appendChild(b); });
  ctl.appendChild(clearBtn);
  function light(ids, conns) {
    Object.entries(OBJ).forEach(([k, o]) => { const dim = ids && !ids.has(k); o.g.classList.toggle('dim', !!dim); o.g.classList.toggle('sel', k === sel); o.lab && o.lab.classList.toggle('dim', !!dim); });
    CONN.forEach(c => { const on = conns && conns.has(c); c.el.classList.toggle('on', !!on); c.el.classList.toggle('dim', !!conns && !on); c.lab && c.lab.classList.toggle('dim', !!conns && !on); });
    clearBtn.hidden = !ids;
  }
  function select(id) {
    route = null; ctl.querySelectorAll('[data-route]').forEach(b => b.classList.remove('on'));
    sel = sel === id ? null : id;
    if (!sel) { light(null, null); return overview(); }
    const conns = new Set(CONN.filter(c => c.a === sel || c.b === sel));
    light(new Set([sel, ...[...conns].flatMap(c => [c.a, c.b])]), conns); show(sel);
  }
  function follow(r) {
    sel = null; route = route === r ? null : r;
    ctl.querySelectorAll('[data-route]').forEach(b => b.classList.toggle('on', b.dataset.route === route));
    if (!route) { light(null, null); return overview(); }
    const conns = new Set(CONN.filter(c => (c.route || []).includes(route)));
    light(new Set([...conns].flatMap(c => [c.a, c.b])), conns);
    const R = spec.routes[route];
    card.innerHTML = `<h3>${esc(R.name)} ${tag(R.status)}</h3><p class="where">${esc(R.where)}</p>${R.d.map(t => `<p>${t}</p>`).join('')}`;
  }
  function clear() { sel = null; route = null; ctl.querySelectorAll('[data-route]').forEach(b => b.classList.remove('on')); light(null, null); overview(); }
  clearBtn.onclick = clear;
  Object.entries(OBJ).forEach(([id, o]) => {
    o.g.addEventListener('click', e => { e.stopPropagation(); select(id); });
    o.g.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(id); } });
  });
  overview();
  const api = { spec, svg, OBJ, CONN, L, select, follow, clear, placeLineLabels };
  FIGS.push(api);
  return api;
}

/* =====================================================================================================
   Figure 1 · Relate in one picture: Neo4j, one file, and three ways to open it, from Relate's README
   ===================================================================================================== */
Figure({ svg:'fig-relate', card:'card-relate', ctl:'ctl-relate', w:1200, h:560, s:16, num:'FIG_001', title:'RELATE IN ONE PICTURE',
  aria:'Isometric drawing of Neo4j on the left, one Relate file in the middle, and three ways to open the file on the right: the command line, a program and an AI agent, with a pull from Neo4j into the file and a push back',
  notes:[{ x:60, y:52, text:'RELATE 1.0 · 11 NOVEMBER 2026 · APACHE 2.0 · FROM NEO4J' },
         { x:60, y:518, text:'ITS OWN LIMITS · THE GRAPH FITS IN MEMORY · ONE GRAPH PER FILE · ONE USER OR AGENT AT A TIME' }],
  parts:[
    { id:'neo4j', draw:'graph', out:true, dashed:true, sx:180, sy:300, lab:['NEO4J', 'THE WHOLE GRAPH · A SERVER'], pos:'below',
      name:'Neo4j', where:'the server that holds the whole graph · not ours',
      d:['Neo4j runs as a server and holds the whole graph for a company: every account, device, address and payment, and the links between them.', 'Relate pulls a piece of it out, by a query, and pushes results back.'],
      files:'Relate README: pull and push' },
    { id:'file', draw:'sheet', hot:true, sx:540, sy:300, lab:['ONE RELATE FILE', 'A PIECE OF THE GRAPH · NO SERVER'], pos:'below',
      name:'One Relate file', where:'a graph database in a single file',
      d:['Relate is a graph database that runs inside a program. A graph is one file. It speaks Cypher, the same query language as Neo4j, and has search by words and by meaning built in.', 'Its own limits: the graph fits in memory, one graph per file, one user or agent at a time. It is not a warehouse.'],
      files:'Relate README, read 16 November 2026' },
    { id:'cli', draw:'terminal', sx:920, sy:140, lab:['THE COMMAND LINE', 'QUERY · PULL · PUSH'], pos:'right',
      name:'The command line', where:'relate query, relate pull, relate push',
      d:['One command opens a file and runs a query. One pulls a slice from Neo4j. One pushes results back, with a dry run first that shows what it would write.', 'The demo store in this repo opens this way, in five minutes.'],
      files:'demo/README.md, “Inspect it yourself”' },
    { id:'program', draw:'machine', sx:920, sy:300, lab:['A PROGRAM', 'PYTHON HERE'], pos:'right',
      name:'A program', where:'Python here; also Node.js, the browser and Rust',
      d:['The same engine runs inside a program, with no server to run. The lab opens its file from Python and hands the slice to the models.', 'An optional server speaks Bolt and HTTP for apps, and MCP for agents.'],
      files:'lab/common/store.py' },
    { id:'agent', draw:'cage', sx:920, sy:455, lab:['AN AI AGENT', 'MCP AND THE SKILL'], pos:'right',
      name:'An AI agent', where:'through MCP and the Relate skill',
      d:['Relate ships a server that speaks MCP, the way agents talk to tools, and a skill that teaches an agent its commands.', 'In the lab the agent reads the file this way, read only.'],
      files:'demo/agent.md' },
  ],
  conns:[
    { a:'neo4j', b:'file', f:'r', t:'l', label:'PULL · A SLICE, BY A QUERY', route:['trip'] },
    { a:'file', b:'neo4j', f:'l', t:'r', lane:180, fk:-.3, tk:-.3, label:'PUSH · DRY RUN FIRST', route:['trip'] },
    { a:'file', b:'cli', f:'r', t:'l', mx:.3, fk:-.3, label:'', route:['readers'] },
    { a:'file', b:'program', f:'r', t:'l', mx:.5, fk:.5, label:'CYPHER · SEARCH', route:['readers'] },
    { a:'file', b:'agent', f:'r', t:'l', mx:.3, fk:1.3, label:'OVER MCP', route:['readers'] },
  ],
  routes:{
    trip:{ name:'The trip', where:'pull a piece out, push results back',
      d:['A query says which piece of the Neo4j graph to copy into the file. Work happens on the file.', 'The push shows what it would write before it writes anything. That dry run is where a person decides.'] },
    readers:{ name:'Who opens the file', where:'the command line, a program, an AI agent',
      d:['The same file opens from the command line, inside a program, or through an AI agent over MCP.', 'No server runs in any of the three.'] },
  },
  overview:{ title:'Relate in one picture', where:'click any part, or a path above',
    d:['Neo4j holds the whole graph on a server. Relate holds a piece of it in one file, with no server, and three ways to open it.', 'Dashed shapes aren’t ours.'],
    order:['neo4j', 'file', 'cli', 'program', 'agent'] },
});

/* =====================================================================================================
   Figure 2 · a ring in plain sight: planted ring R0006 from the demo data
   ===================================================================================================== */
const RING = ['A0000897', 'A0001287', 'A0001723', 'A0001818', 'A0005373'];
const ringAccount = (id, i) => ({ id:'a' + i, draw:'account', sx:380 + i * 125, sy:290, pos:'none',
  name:'Account ' + id, where:'one of five, opened within weeks of each other',
  d:['On its own, an ordinary new account: money in, money out.', 'It shares one device and one address with the other four, and pays them in a loop. The lab scored each of the five 0.90.'],
  files:'demo/README.md, steps 4 and 8' });
Figure({ svg:'fig-ring', card:'card-ring', ctl:'ctl-ring', w:1200, h:540, s:16, num:'FIG_002', title:'A RING IN PLAIN SIGHT',
  aria:'Isometric drawing of five new bank accounts in a row, linked up to one shared laptop and down to one shared house, with money coming in from outside and going round between them',
  notes:[{ x:60, y:52, text:'RING R0006 · FIVE NEW ACCOUNTS · PLANTED IN THE DEMO DATA' }],
  parts:[
    { id:'senders', draw:'people', out:true, dashed:true, sx:170, sy:300, lab:['PEOPLE OUTSIDE', 'SEND MONEY IN'], pos:'below',
      name:'People outside the ring', where:'other accounts in the demo bank',
      d:['Money comes into the ring from accounts outside it.', 'A payment in is the most ordinary thing an account does. Nothing here looks wrong yet.'],
      files:'generator/' },
    ...RING.map(ringAccount),
    { id:'device', draw:'terminal', sx:630, sy:150, lab:['ONE DEVICE', 'D0000896'], pos:'right',
      name:'One device', where:'D0000896, seen on all five accounts',
      d:['All five accounts were used from the same device.', 'Five or more accounts on one device is a warning sign. A shared family computer is the innocent explanation to rule out first.'],
      files:'factors/shared_device.cypher' },
    { id:'house', draw:'house', sx:630, sy:470, lab:['ONE ADDRESS', 'AD0000896'], pos:'right',
      name:'One address', where:'AD0000896, given by all five accounts',
      d:['All five accounts give the same address.', 'A household shares an address too. The lab’s summary names that explanation, so the reviewer checks it first.'],
      files:'factors/new_accounts_shared_identifier.cypher' },
  ],
  conns:[
    { a:'senders', b:'a0', f:'r', t:'l', label:'MONEY IN', tk:.85, route:['alone', 'money'] },
    ...[0, 1, 2, 3].map(i => ({ a:'a' + i, b:'a' + (i + 1), f:'r', t:'l', label:'', route:['money'] })),
    { a:'a4', b:'a0', f:'r', t:'l', lane:82, fk:.15, tk:.15, label:'AND ROUND AGAIN · 21 CYCLES', route:['money'] },
    ...[0, 1, 2, 3, 4].map(i => ({ a:'a' + i, b:'device', f:'t', t:'b', tk:-.5 + i * .5, label:i === 2 ? 'SAME DEVICE' : '', route:['shared'] })),
    ...[0, 1, 2, 3, 4].map(i => ({ a:'a' + i, b:'house', f:'b', t:'t', tk:-.5 + i * .5, label:i === 2 ? 'SAME ADDRESS' : '', route:['shared'] })),
  ],
  routes:{
    alone:{ name:'One at a time', where:'what a check of each account on its own sees',
      d:['Take any one of the five: a new account, a few payments in, a few out.', 'A rule that looks at accounts one at a time passes all five.'] },
    shared:{ name:'What they share', where:'the links a table of accounts doesn’t keep',
      d:['All five use the same device and give the same address.', 'Those links are what a graph stores, and they are where the lab starts.'] },
    money:{ name:'The money', where:'in from outside, then round between the five',
      d:['Money comes in from outside and is passed from one account to the next, then back to the first.', 'In this run it went round 21 times in ten and a half days.'] },
  },
  overview:{ title:'A ring, in plain sight', where:'click any part, or a path above',
    d:['Five new accounts. Each looks ordinary on its own. Together they share one device and one address, and pass money round in a loop.', 'This is ring R0006, planted in the demo data. The lab found it without being told. Dashed shapes are outside the ring.'],
    order:['senders', 'a0', 'a1', 'a2', 'a3', 'a4', 'device', 'house'] },
});

/* =====================================================================================================
   Figure 3 · the loop: pull a slice, find, learn, explain, review, push back
   ===================================================================================================== */
Figure({ svg:'fig-loop', card:'card-loop', ctl:'ctl-loop', w:1200, h:560, s:15, num:'FIG_003', title:'THE LOOP',
  aria:'Isometric drawing of the loop: Neo4j, a pull into one Relate file, find, learn, score and explain, a person reviewing, and a push back to Neo4j after one confirm, with an AI agent above reading read-only and acting through nine tools',
  zones:[{ x:206, y:226, w:780, h:226, label:'' }],
  notes:[{ x:216, y:442, text:'SUBGRAPH-LAB · ONE CONTAINER BESIDE NEO4J' }],
  parts:[
    { id:'neo4j', draw:'graph', out:true, dashed:true, sx:110, sy:345, lab:['NEO4J', 'THE WHOLE GRAPH'], pos:'below',
      name:'Neo4j', where:'your graph, on Aura or your own server · not ours',
      d:['Neo4j holds the whole graph: every account, device, address and payment, and the links between them.', 'The lab never changes it without a review and one confirm.'],
      files:'compose.yaml, .env.example' },
    { id:'store', draw:'sheet', hot:true, sx:290, sy:340, lab:['RELATE STORE', 'ONE FILE PER RUN'], pos:'below',
      name:'A Relate store', where:'the file from Figure 1, made fresh for each run',
      d:['This is the Relate file from Figure 1. Relate copies a slice of the graph into it, and the lab works on the file and leaves Neo4j alone.', 'Search and queries run on it with no server. The demo run’s store is in the repo, 9.8 MB.'],
      files:'lab/roundtrip/pull.py, demo/store.rl' },
    { id:'find', draw:'filter', sx:430, sy:340, lab:['FIND', 'SEARCH, THEN THE LINKS'], pos:'below',
      name:'Find the part that matters', where:'search by meaning and by words, then follow the links',
      d:['Ask in plain words, like “new accounts sharing one device and address”. The lab finds the accounts that match, then the accounts linked to them.', 'The slice is sized before anything runs, so nobody pulls more than they meant to.'],
      files:'lab/slice/search.py, lab/slice/estimate.py' },
    { id:'learn', draw:'machine', sx:570, sy:340, lab:['LEARN', 'OPEN-SOURCE MODELS'], pos:'below',
      name:'Open-source models', where:'graph models the open-source world already maintains',
      d:['Models learn what normal looks like from the links and from the timing of payments.', 'They can be swapped. The lab hands each one the slice in the shape it expects.'],
      files:'lab/adapters/, examples/' },
    { id:'explain', draw:'stack', hot:true, sx:720, sy:340, lab:['SCORE AND EXPLAIN', 'A NUMBER AND A REASON'], pos:'below',
      name:'A score and a reason', where:'for every account and every group of accounts',
      d:['Every account gets a risk score, and so does every group, with the arithmetic shown.', 'A short summary says why. It is written only from the evidence and names the innocent explanation to rule out first.'],
      files:'lab/scoring/, lab/summaries/' },
    { id:'review', draw:'desk', sx:880, sy:345, lab:['A PERSON REVIEWS', 'DRY RUN, THEN ONE CONFIRM'], pos:'below',
      name:'A person reviews', where:'the Review screen',
      d:['Before anything goes back, the reviewer sees what would change: Relate’s dry run, and the lab’s own count of what is new.', 'Then one confirm. Every write carries the run’s id, and a run can be taken back.'],
      files:'lab/roundtrip/push.py, lab/roundtrip/delta.py, lab/roundtrip/retract.py' },
    { id:'agent', draw:'cage', sx:575, sy:150, lab:['AN AI AGENT', 'TWO MCP SERVERS, NO OTHER TOOLS'], pos:'right',
      name:'An AI agent', where:'Claude Code, in the recorded session',
      d:['An agent can do the reviewer’s legwork through the lab’s tools. It reads the store through Relate, read only.', 'It can’t change Neo4j without the same dry run and confirm a person needs.'],
      files:'demo/agent.md, lab/api/mcp_tools.py' },
  ],
  conns:[
    { a:'neo4j', b:'store', f:'r', t:'l', label:'PULL', route:['trip'] },
    { a:'store', b:'find', f:'r', t:'l', label:'', route:['trip'] },
    { a:'find', b:'learn', f:'r', t:'l', label:'', route:['trip'] },
    { a:'learn', b:'explain', f:'r', t:'l', label:'', route:['trip'] },
    { a:'explain', b:'review', f:'r', t:'l', label:'', route:['trip', 'person'] },
    { a:'review', b:'neo4j', f:'r', t:'b', lane:505, label:'PUSH BACK · AFTER ONE CONFIRM', route:['trip', 'person'] },
    { a:'agent', b:'store', f:'l', t:'t', label:'READS · READ ONLY', route:['agent'] },
    { a:'agent', b:'review', f:'r', t:'t', label:'ACTS · NINE TOOLS', route:['agent'] },
  ],
  routes:{
    trip:{ name:'The round trip', where:'from Neo4j and back',
      d:['Pull a slice into a Relate file, find what matters, let the models learn, score and explain, review, push back.', 'From starting the lab to a reviewed push took 160 seconds in the measured demo run.'] },
    person:{ name:'Where a person decides', where:'the Review screen',
      d:['Nothing goes back to Neo4j until a person has seen the dry run and pressed confirm.', 'The reviewer can also mark a group confirmed, a false positive, or needing more work. That decision goes back too.'] },
    agent:{ name:'What the agent can touch', where:'two MCP servers, no other tools',
      d:['It reads everything in the store, read only, and acts through the lab’s nine tools.', 'The one tool that writes to Neo4j refuses without a fresh review and a confirm.'] },
  },
  overview:{ title:'The loop', where:'click any part, or a path above',
    d:['Left to right: take a slice of the graph out into a Relate file, learn from it, explain what was found, and write it back after a review.', 'Dashed shapes aren’t ours.'],
    order:['neo4j', 'store', 'find', 'learn', 'explain', 'review', 'agent'] },
});

/* =====================================================================================================
   Figure 4 · one loop, more industries: the packs on the 2.0 row of architecture-docs/99-build-status.md
   ===================================================================================================== */
const PACK = (id, sx, name, means) => ({ id, draw:'sheet', dashed:true, sx, sy:160, lab:[name.toUpperCase(), 'NOT YET'], pos:'above',
  name, where:'a pack on the plan · not built', status:['not', 'Not built'],
  d:[means, 'A pack is a recipe, a factor file, an example and a review checklist. This one is on the 2.0 row of the build plan.'],
  files:'architecture-docs/99-build-status.md' });
Figure({ svg:'fig-next', card:'card-next', ctl:'ctl-next', w:1200, h:500, s:17, num:'FIG_004', title:'ONE LOOP, MORE INDUSTRIES',
  aria:'Isometric drawing of five packs above one loop: fraud, which ships today, and four dashed packs not built yet, all feeding the same loop, which pushes back to Neo4j',
  parts:[
    { id:'fraud', draw:'sheet', hot:true, sx:200, sy:160, lab:['FRAUD', 'SHIPS TODAY'], pos:'above',
      name:'Fraud', where:'the pack that ships today',
      d:['Rings of accounts that share devices, addresses and emails, and pass money between them.', 'The pack is the factor file (the warning signs and how much each counts), the demo data and a recipe.'],
      files:'factors/factors.yaml, generator/, examples/' },
    PACK('fincrime', 390, 'Financial crime', 'Crime that moves money through accounts, beyond the rings the fraud pack finds.'),
    PACK('churn', 580, 'Churn', 'Customers who are about to leave.'),
    PACK('supply', 770, 'Supply chain', 'Suppliers, parts and shipments, and how they depend on each other.'),
    PACK('identity', 960, 'Identity', 'Whether two records are the same person or the same company.'),
    { id:'loop', draw:'machine', sx:580, sy:390, lab:['THE SAME LOOP', 'PULL · FIND · LEARN · EXPLAIN · REVIEW · PUSH'], pos:'below',
      name:'The same loop', where:'nothing in it is specific to fraud',
      d:['Only the factor file and the demo data are about fraud. The pull, the search, the models, the review and the push are the same for any use case.', 'A new pack plugs into the loop. The agent uses the same tools.'],
      files:'lab/' },
    { id:'neo4j3', draw:'graph', out:true, dashed:true, sx:960, sy:400, lab:['NEO4J', 'THE SAME PUSH'], pos:'below',
      name:'Neo4j', where:'your graph · not ours',
      d:['Every pack writes back the same way: a dry run, one confirm, and the run’s id on every write.'],
      files:'lab/roundtrip/push.py' },
  ],
  conns:[
    { a:'fraud', b:'loop', f:'b', t:'t', tk:-.5, my:.62, label:'TODAY', route:['today'] },
    ...['fincrime', 'churn', 'supply', 'identity'].map((id, i) => ({ a:id, b:'loop', f:'b', t:'t', tk:i * .5, my:.4, cls:'gap', label:'', route:['next'] })),
    { a:'loop', b:'neo4j3', f:'r', t:'l', label:'SAME REVIEW · SAME PUSH', route:['today', 'next'] },
  ],
  routes:{
    today:{ name:'Today', where:'the fraud pack',
      d:['The fraud pack runs the whole loop today, from the pull to the reviewed push.', 'Everything on this page was made with it.'] },
    next:{ name:'Next', where:'four packs on the 2.0 row · not built', status:['not', 'Not built'],
      d:['Financial crime, churn, supply chain and identity, each a pack for the same loop.', 'That is the plan. Today the lab ships the fraud pack.'] },
  },
  overview:{ title:'One loop, more industries', where:'click any part, or a path above',
    d:['Fraud ships today. The four dashed packs are the plan.', 'Dashed means not built yet, or not ours.'],
    order:['fraud', 'fincrime', 'churn', 'supply', 'identity', 'loop', 'neo4j3'] },
});

/* ---------- Escape undoes every figure; then labels and motion once the fonts are in ---------- */
document.addEventListener('keydown', e => { if (e.key === 'Escape') FIGS.forEach(f => f.clear()); });
const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
let A = null;
try { A = await import('https://cdn.jsdelivr.net/npm/animejs@4.5.0/dist/bundles/anime.esm.min.js'); } catch { A = null; }
await document.fonts.ready;
FIGS.forEach(f => f.placeLineLabels());
if (A && !still && 'IntersectionObserver' in window) {
  /* Each figure fades in the first time it scrolls into view. Inline styles are cleared at the end, so the
     click-to-dim classes keep working; printing shows everything at once. */
  const targets = f => ({ lines:f.CONN.map(c => c.el), parts:[f.L.back, f.L.front, f.L.labels].flatMap(g => [...g.children]) });
  const reset = els => els.forEach(e => { e.style.opacity = ''; e.style.transform = ''; });
  FIGS.forEach(f => { const t = targets(f); [...t.lines, ...t.parts].forEach(e => { e.style.opacity = 0; }); f.waiting = true; });
  const play = f => {
    if (!f.waiting) return; f.waiting = false;
    const t = targets(f);
    A.animate(t.lines, { opacity:[0, 1], duration:650, delay:A.stagger(55, { start:250 }), ease:'outQuad', onComplete:() => reset(t.lines) });
    A.animate(t.parts, { opacity:[0, 1], translateY:[6, 0], duration:500, delay:A.stagger(30), ease:'outExpo', onComplete:() => reset(t.parts) });
  };
  const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { io.unobserve(e.target); play(FIGS.find(f => f.svg === e.target)); } }), { threshold:.2 });
  FIGS.forEach(f => io.observe(f.svg));
  addEventListener('beforeprint', () => FIGS.forEach(f => { f.waiting = false; const t = targets(f); reset([...t.lines, ...t.parts]); }));
}
window.__figs = { FIGS, ready:true };
