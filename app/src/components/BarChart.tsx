// A bar chart in plain SVG: one series, one baseline, a value axis with a few round ticks, a label axis with a few
// evenly spaced labels. Bars are at most 24px thick, round at the top (4px) and square at the baseline, 2px apart. The
// chart draws; the caller says what the bars mean. Hover is reported through `onHover`, so the caller can show a
// readout; each bar also carries a native tooltip. The drawing is a picture of the data (role "img"): the numbers
// themselves belong in a table next to it, which the caller provides.
import { useState } from "react";
import { formatInt } from "./format";

export interface Bar {
  /** Identifies the bar to `onHover` and `marker`. Unique in the chart. */
  key: string;
  /** The bar in full, for the native tooltip: "Mon, Sep 14, 2026". */
  label: string;
  value: number;
  /** The short text under the axis when this bar is one of the labelled ones: "Sep 14". Defaults to `label`. */
  tick?: string;
  /** Drawn grey: part of the picture but outside what the screen is about (days after the as-of date). */
  muted?: boolean;
}

export interface BarChartProps {
  bars: Bar[];
  /** What is plotted, in words: the chart's accessible name. */
  label: string;
  /** Text for a value in the native tooltips: the whole number. Defaults to en-US digits with separators. */
  formatValue?: (n: number) => string;
  /** Text for a tick on the value axis. Defaults to whole numbers, compact from 10,000 (12.3K). */
  formatTick?: (n: number) => string;
  /** True for counts: the axis never steps by less than 1. */
  whole?: boolean;
  /** The top of the value axis is at least this, so a chart that is redrawn from new data keeps its scale. */
  max?: number;
  /** The key of a bar to draw a vertical line after (its right edge): "up to here". */
  marker?: string | null;
  /** Called with the key of the bar under the pointer, and with null when the pointer leaves the bars. */
  onHover?: (key: string | null) => void;
  /** Height of the drawing in SVG units (its width is 720). Default 260. */
  height?: number;
}

const WIDTH = 720;
const PAD = { top: 12, right: 12, bottom: 30, left: 52 };
const MAX_BAR = 24;
const FONT = 11;
const TICK_SPACING = 90; // SVG units a label needs between itself and the next

const COMPACT = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

/** 1,284 under 10,000; 12.3K, 4.2M above. */
export function formatCompact(n: number): string {
  return Math.abs(n) < 10_000 ? formatInt(n) : COMPACT.format(n);
}

/** A top for the value axis and the ticks up to it, in steps of 1, 2, 2.5, 5 times a power of ten. */
export function niceScale(max: number, whole = false, count = 4): { top: number; ticks: number[] } {
  if (!(max > 0)) return { top: 1, ticks: [0, 1] };
  const raw = max / count;
  const unit = 10 ** Math.floor(Math.log10(raw));
  let step = [1, 2, 2.5, 5, 10].map((m) => m * unit).find((s) => s >= raw) ?? raw;
  if (whole && step < 1) step = 1;
  const n = Math.ceil(max / step - 1e-9);
  return { top: +(n * step).toPrecision(12), ticks: Array.from({ length: n + 1 }, (_, i) => +(i * step).toPrecision(12)) };
}

/** Which bars carry an axis label: the first, the last and evenly spaced ones between, as many as fit. */
function tickIndexes(count: number, plotWidth: number): number[] {
  if (count <= 1) return count === 1 ? [0] : [];
  const labels = Math.min(count, Math.max(2, Math.min(8, Math.floor(plotWidth / TICK_SPACING))));
  return [...new Set(Array.from({ length: labels }, (_, t) => Math.round((t * (count - 1)) / (labels - 1))))];
}

/** A bar's outline: a 4px radius at the top, square at the baseline; a plain rectangle when it is too thin to round. */
function barPath(x: number, y: number, w: number, h: number): string {
  const r = Math.min(4, w / 2, h);
  if (w < 6 || r <= 0) return `M${x},${y + h}V${y}H${x + w}V${y + h}Z`;
  return `M${x},${y + h}V${y + r}A${r},${r} 0 0 1 ${x + r},${y}H${x + w - r}A${r},${r} 0 0 1 ${x + w},${y + r}V${y + h}Z`;
}

export function BarChart({ bars, label, formatValue = formatInt, formatTick = formatCompact, whole = false, max = 0, marker = null, onHover, height = 260 }: BarChartProps) {
  const [hover, setHover] = useState<string | null>(null);
  if (bars.length === 0) return <p className="text-sm text-slate-500">Nothing to plot yet.</p>;

  const plotW = WIDTH - PAD.left - PAD.right;
  const plotH = height - PAD.top - PAD.bottom;
  const { top, ticks } = niceScale(bars.reduce((m, b) => Math.max(m, b.value), max), whole);
  const band = plotW / bars.length;
  const gap = band >= 4 ? 2 : band >= 2 ? 1 : 0;
  const barW = Math.min(MAX_BAR, Math.max(1, band - gap));
  const yOf = (v: number) => PAD.top + plotH - (v / top) * plotH;
  const baseline = PAD.top + plotH;
  const markerAt = marker === null ? -1 : bars.findIndex((b) => b.key === marker);

  const over = (key: string | null) => { setHover(key); onHover?.(key); };

  return (
    <div className="overflow-x-auto">
      <svg viewBox={`0 0 ${WIDTH} ${height}`} role="img" aria-label={label} className="block h-auto w-full min-w-[520px]"
        onMouseLeave={() => over(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={WIDTH - PAD.right} y1={yOf(t)} y2={yOf(t)} className={t === 0 ? "stroke-slate-300" : "stroke-slate-100"} strokeWidth={1} />
            <text x={PAD.left - 8} y={yOf(t)} textAnchor="end" dominantBaseline="central" fontSize={FONT} className="fill-slate-600">
              {formatTick(t)}
            </text>
          </g>
        ))}

        {bars.map((b, i) => {
          const h = b.value > 0 ? Math.max(1, baseline - yOf(b.value)) : 0;
          const x = PAD.left + i * band + (band - barW) / 2;
          return h === 0 ? null : (
            <path key={b.key} data-bar={b.key} data-muted={b.muted ? "true" : undefined} d={barPath(x, baseline - h, barW, h)}
              className={b.muted ? (hover === b.key ? "fill-slate-300" : "fill-slate-200") : (hover === b.key ? "fill-indigo-800" : "fill-indigo-600")} />
          );
        })}

        {markerAt >= 0 && (
          <line data-marker={marker} x1={PAD.left + (markerAt + 1) * band} x2={PAD.left + (markerAt + 1) * band}
            y1={PAD.top} y2={baseline} className="stroke-slate-800" strokeWidth={1.5} />
        )}

        {tickIndexes(bars.length, plotW).map((i, n, all) => {
          const edge = n === 0 ? "start" : n === all.length - 1 ? "end" : "middle";
          const x = edge === "start" ? PAD.left + i * band : edge === "end" ? PAD.left + (i + 1) * band : PAD.left + (i + 0.5) * band;
          return (
            <text key={bars[i].key} x={x} y={baseline + 8} textAnchor={edge} dominantBaseline="hanging" fontSize={FONT} className="fill-slate-600">
              {bars[i].tick ?? bars[i].label}
            </text>
          );
        })}

        {/* The whole height of a day is its target, not just the painted bar. */}
        {bars.map((b, i) => (
          <rect key={b.key} x={PAD.left + i * band} y={PAD.top} width={band} height={plotH} fill="transparent"
            onMouseEnter={() => over(b.key)}>
            <title>{`${b.label}: ${formatValue(b.value)}`}</title>
          </rect>
        ))}
      </svg>
    </div>
  );
}
