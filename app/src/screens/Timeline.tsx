// Timeline (page 06): a slider that re-queries the run's store "as of t" on the server, and a chart of the slice's
// activity day by day up to that point. The screen follows one run (the run context's, or `?run=` in the address).
// It asks once for the whole range, to learn the first and last day, then asks again as of the day the slider is on:
// on release, or after a short pause, never on every pixel. An answer that has been overtaken by a newer question
// is dropped when it arrives. The browser does no querying of its own (the in-browser version is 1.1), and nothing
// here writes anywhere.
import { useEffect, useId, useMemo, useRef, useState, type ChangeEvent, type PointerEvent } from "react";
import { api } from "../api/client";
import type { Timeline as TimelineData, TimelinePoint } from "../api/types";
import { BarChart, type Bar } from "../components/BarChart";
import { errorMessage, formatInt } from "../components/format";
import { NoRunNotice, useRunRef } from "../components/RunRef";
import { Button, Card, cx, Notice, Page, Stat, Table, Tabs, type Column } from "../components/ui";

/** How long the slider rests before the lab is asked. Letting go of it asks at once. */
export const PAUSE_MS = 400;

// ---- days ----------------------------------------------------------------------------------------------------------
// A day is "YYYY-MM-DD" in UTC, the way the lab cuts days. Those strings sort and compare as dates do.
const DAY_MS = 86_400_000;
const at = (day: string) => Date.parse(`${day}T00:00:00Z`);

/** The UTC day of an ISO timestamp, or null when it is not one. */
function dayOf(iso: string): string | null {
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : new Date(t).toISOString().slice(0, 10);
}

/** Every day from `first` to `last`, both included. */
function daysFrom(first: string, last: string): string[] {
  const n = Math.round((at(last) - at(first)) / DAY_MS);
  return Array.from({ length: n + 1 }, (_, i) => new Date(at(first) + i * DAY_MS).toISOString().slice(0, 10));
}

const dateFormat = (o: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat("en-US", { timeZone: "UTC", ...o });
const LONG = dateFormat({ weekday: "short", year: "numeric", month: "short", day: "numeric" });
const MEDIUM = dateFormat({ year: "numeric", month: "short", day: "numeric" });
const SHORT = dateFormat({ month: "short", day: "numeric" });
const AMOUNT = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmt = (f: Intl.DateTimeFormat, day: string) => f.format(at(day));

// ---- what the lab said ---------------------------------------------------------------------------------------------
interface Answer { day: string; list: TimelinePoint[]; byDay: Map<string, TimelinePoint> }

/** The points of an answer by day. A point past the day that was asked for is not this answer's to show. */
function answerOf(day: string, data: TimelineData): Answer {
  const list = data.points.filter((p) => (dayOf(p.at) ?? "9") <= day);
  return { day, list, byDay: new Map(list.map((p) => [dayOf(p.at) ?? p.at, p])) };
}

interface Full { data: TimelineData; days: string[]; byDay: Map<string, TimelinePoint> }
type Load = { kind: "loading" } | { kind: "error"; text: string } | { kind: "empty" } | { kind: "ready"; full: Full };

function readRange(data: TimelineData): Load {
  if (data.points.length === 0) return { kind: "empty" };
  const first = dayOf(data.start);
  const last = dayOf(data.end);
  if (first === null || last === null || last < first) {
    return { kind: "error", text: `The lab gave a range that is not made of dates (${data.start} to ${data.end}).` };
  }
  const byDay = new Map(data.points.map((p) => [dayOf(p.at) ?? p.at, p]));
  return { kind: "ready", full: { data, days: daysFrom(first, last), byDay } };
}

type Measure = "payments" | "amount";
const measureOf = (p: TimelinePoint | undefined, measure: Measure) => (p ? p[measure] : 0);
const MEASURES = [{ id: "payments", label: "Payments" }, { id: "amount", label: "Amount" }];
const PURPOSE = "Slide to a day and the lab asks the run's store what it held as of then: payments, amount and active accounts, day by day.";

// ---- the screen ----------------------------------------------------------------------------------------------------
export default function Timeline() {
  const { runId, invalid } = useRunRef();
  if (!runId) return <Page title="Timeline" purpose={PURPOSE}><NoRunNotice invalid={invalid} /></Page>;
  return <TimelineFor key={runId} runId={runId} />;
}

/** One run: asks for the whole range, then hands the screen to `Slider`. */
function TimelineFor({ runId }: { runId: string }) {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [round, setRound] = useState(0); // bumped by "Try again"

  useEffect(() => {
    let live = true;
    setLoad({ kind: "loading" });
    api.timeline(runId).then(
      (data) => { if (live) setLoad(readRange(data)); },
      (e) => { if (live) setLoad({ kind: "error", text: errorMessage(e) }); },
    );
    return () => { live = false; };
  }, [runId, round]);

  return (
    <Page title="Timeline" purpose={PURPOSE}>
      {load.kind === "loading" && <p role="status" className="text-sm text-slate-600">Loading the timeline for run {runId}…</p>}
      {load.kind === "error" && (
        <Notice kind="error" title="The lab did not answer for this run">
          <p>{load.text}</p>
          <div className="mt-3"><Button onClick={() => setRound((r) => r + 1)}>Try again</Button></div>
        </Notice>
      )}
      {load.kind === "empty" && (
        <Notice kind="info" title="No payments in this run's store">There is no timeline until the store holds payments. If the run is still pulling, look again when it is done.</Notice>
      )}
      {load.kind === "ready" && <Slider key={round} runId={runId} full={load.full} />}
    </Page>
  );
}

function Slider({ runId, full }: { runId: string; full: Full }) {
  const { days } = full;
  const last = days.length - 1;
  const sliderId = useId();
  const statusId = useId();

  // The slider's place, and the answer on screen: they differ while the lab is being asked.
  const [index, setIndex] = useState(last);
  const [answer, setAnswer] = useState<Answer>(() => answerOf(days[last], full.data)); // the whole range is the answer as of its last day
  const [pending, setPending] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [measure, setMeasure] = useState<Measure>("payments");
  const [tableOpen, setTableOpen] = useState(false);

  const timer = useRef<ReturnType<typeof setTimeout>>();
  const sequence = useRef(0); // the number of the latest question; an answer to an older one is dropped
  const asked = useRef(days[last]); // the day last asked (or on screen), so the same day is not asked twice
  const shown = useRef(days[last]);
  useEffect(() => () => { sequence.current += 1; clearTimeout(timer.current); }, []);

  const ask = (i: number) => {
    clearTimeout(timer.current);
    const day = days[i];
    if (!day || day === asked.current) return;
    asked.current = day;
    const mine = ++sequence.current;
    setPending(day); setProblem(null);
    api.timeline(runId, day).then(
      (data) => {
        if (mine !== sequence.current) return;
        shown.current = day;
        setAnswer(answerOf(day, data)); setPending(null);
      },
      (e) => {
        if (mine !== sequence.current) return;
        asked.current = shown.current; // the day on screen is still the day on screen; asking this one again is allowed
        setPending(null); setProblem(errorMessage(e));
      },
    );
  };

  const onSlide = (e: ChangeEvent<HTMLInputElement>) => {
    const i = Math.min(last, Math.max(0, Number(e.target.value)));
    setIndex(i); setProblem(null);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => ask(i), PAUSE_MS);
  };
  const onRelease = (e: PointerEvent<HTMLInputElement>) => ask(Math.min(last, Math.max(0, Number(e.currentTarget.value))));

  const draftDay = days[index];
  const shownDay = answer.day;
  const busy = pending !== null || (problem === null && draftDay !== shownDay);

  // The chart: every day of the range. Up to the day on screen the bars are the lab's answer for that day; after it
  // they are the whole range's, in grey.
  const manyYears = days[0].slice(0, 4) !== days[last].slice(0, 4);
  const pointOf = (day: string) => (day <= shownDay ? answer.byDay.get(day) : full.byDay.get(day));
  const bars = useMemo<Bar[]>(() => days.map((day) => ({
    key: day, label: fmt(LONG, day), tick: fmt(manyYears ? MEDIUM : SHORT, day), muted: day > answer.day,
    value: measureOf(day <= answer.day ? answer.byDay.get(day) : full.byDay.get(day), measure),
  })), [days, answer, full, measure, manyYears]);
  const top = full.data.points.reduce((n, p) => Math.max(n, measureOf(p, measure)), 0); // the scale does not move with the slider

  // The totals are the answer's own points, so they agree with the bars and the table.
  const payments = answer.list.reduce((n, p) => n + p.payments, 0);
  const amount = Math.round(answer.list.reduce((n, p) => n + p.amount, 0) * 100) / 100;
  const mostAccounts = answer.list.reduce((n, p) => Math.max(n, p.accounts), 0);

  // The readout: the day under the pointer, or the day the slider is on.
  const readDay = hover ?? draftDay;
  const read = pointOf(readDay);
  const columns: Column<TimelinePoint>[] = [
    { key: "at", header: "Day", render: (p) => fmt(MEDIUM, dayOf(p.at) ?? "") },
    { key: "payments", header: "Payments", align: "right", render: (p) => formatInt(p.payments) },
    { key: "amount", header: "Amount", align: "right", render: (p) => AMOUNT.format(p.amount) },
    { key: "accounts", header: "Accounts active", align: "right", render: (p) => formatInt(p.accounts) },
  ];

  return (
    <>
      <Card>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <label htmlFor={sliderId} className="text-sm font-medium text-slate-800">As of</label>
          <output htmlFor={sliderId} className="text-lg font-semibold text-slate-900">{fmt(LONG, draftDay)}</output>
        </div>
        <input id={sliderId} type="range" min={0} max={last} step={1} value={index} disabled={last < 1}
          aria-valuetext={fmt(LONG, draftDay)} aria-describedby={statusId}
          onChange={onSlide} onPointerUp={onRelease}
          className="block w-full accent-indigo-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600" />
        <div className="flex justify-between text-sm text-slate-600">
          <span>{fmt(MEDIUM, days[0])}</span>
          <span>{fmt(MEDIUM, days[last])}</span>
        </div>
        <p id={statusId} role="status" className="text-sm text-slate-600">
          {busy ? "Asking the lab…" : `Showing the run's store up to the end of ${fmt(MEDIUM, shownDay)}.`}
        </p>
        {problem && (
          <Notice kind="error" title={`The lab could not answer for ${fmt(MEDIUM, draftDay)}`}>
            <p>{problem}</p>
            <p className="mt-1">The screen still shows {fmt(MEDIUM, shownDay)}.</p>
            <div className="mt-3"><Button onClick={() => ask(index)}>Try again</Button></div>
          </Notice>
        )}
      </Card>

      <section aria-label="Totals up to the as-of day" className={cx("grid grid-cols-2 gap-3 md:grid-cols-4", busy && "opacity-60")}>
        <Stat label="Days" value={formatInt(days.indexOf(shownDay) + 1)} hint={`${formatInt(answer.list.length)} with payments`} />
        <Stat label="Payments" value={formatInt(payments)} />
        <Stat label="Amount" value={AMOUNT.format(amount)} />
        <Stat label="Most accounts in a day" value={formatInt(mostAccounts)} />
      </section>

      <Card title="Activity by day" description="One bar a day. Days after the as-of day are grey; the line marks where it ends.">
        <Tabs tabs={MEASURES} value={measure} onChange={(id) => setMeasure(id as Measure)} label="What the bars show">
          <div className={cx("space-y-3", busy && "opacity-60")}>
            <div role="group" aria-label="Selected day" className="min-h-[3.25rem] text-sm text-slate-600">
              <p className="font-medium text-slate-900">
                {fmt(LONG, readDay)}
                {readDay === draftDay && <span className="font-normal text-slate-600"> · the as-of day</span>}
                {readDay > draftDay && <span className="font-normal text-slate-600"> · after the as-of day</span>}
              </p>
              <p>
                {read
                  ? `${formatInt(read.payments)} payments · ${AMOUNT.format(read.amount)} · ${formatInt(read.accounts)} accounts active`
                  : "No payments"}
              </p>
            </div>
            <BarChart bars={bars} label={`${measure === "amount" ? "Amount" : "Payments"} per day, ${fmt(MEDIUM, days[0])} to ${fmt(MEDIUM, days[last])}`}
              formatValue={measure === "amount" ? (n) => AMOUNT.format(n) : formatInt} whole={measure === "payments"} max={top}
              marker={draftDay} onHover={setHover} />
          </div>
        </Tabs>
        <div>
          <Button aria-expanded={tableOpen} aria-controls={`${statusId}-table`} onClick={() => setTableOpen((o) => !o)}>
            {tableOpen ? "Hide the days as a table" : "Show the days as a table"}
          </Button>
          {tableOpen && (
            <div id={`${statusId}-table`} className="mt-3 max-h-80 overflow-y-auto">
              <Table columns={columns} rows={answer.list} caption="Days up to the as-of day" rowKey={(p) => p.at}
                empty="No payments up to this day." />
            </div>
          )}
        </div>
      </Card>
    </>
  );
}
