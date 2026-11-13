import { fireEvent, render, screen, within } from "@testing-library/react";
import { BarChart, formatCompact, niceScale, type Bar } from "./BarChart";

const bars = (values: number[], extra: (i: number) => Partial<Bar> = () => ({})): Bar[] =>
  values.map((value, i) => ({ key: `d${i}`, label: `Day ${i}`, tick: `D${i}`, value, ...extra(i) }));

test("the axis tops out at a round number and steps by 1, 2, 2.5 or 5 times a power of ten", () => {
  expect(niceScale(58)).toEqual({ top: 60, ticks: [0, 20, 40, 60] });
  expect(niceScale(9876.5)).toEqual({ top: 10000, ticks: [0, 2500, 5000, 7500, 10000] });
  expect(niceScale(0)).toEqual({ top: 1, ticks: [0, 1] });
  expect(niceScale(0.3).top).toBeGreaterThanOrEqual(0.3);
});

test("a count axis never steps by less than one", () => {
  expect(niceScale(3, true)).toEqual({ top: 3, ticks: [0, 1, 2, 3] });
  expect(niceScale(1, true)).toEqual({ top: 1, ticks: [0, 1] });
});

test("axis numbers are written out under 10,000 and compact from there", () => {
  expect(formatCompact(1284)).toBe("1,284");
  expect(formatCompact(12_300)).toBe("12.3K");
  expect(formatCompact(4_200_000)).toBe("4.2M");
});

test("it is a picture with a name, one bar per day that has a value, and grey for the muted ones", () => {
  const { container } = render(
    <BarChart label="Payments per day" bars={bars([5, 0, 8, 3], (i) => ({ muted: i >= 2 }))} />);
  const chart = screen.getByRole("img", { name: "Payments per day" });
  const drawn = [...container.querySelectorAll("[data-bar]")];
  expect(drawn.map((b) => b.getAttribute("data-bar"))).toEqual(["d0", "d2", "d3"]); // a day at zero draws nothing
  expect(drawn.map((b) => b.getAttribute("data-muted"))).toEqual([null, "true", "true"]);
  expect(chart.querySelectorAll("[data-bar]")).toHaveLength(3);
});

test("the value axis has round ticks and the label axis names its first and last bars", () => {
  render(<BarChart label="x" bars={bars([5, 10, 58, 3])} />);
  const chart = screen.getByRole("img");
  for (const tick of ["0", "20", "40", "60"]) expect(within(chart).getByText(tick)).toBeInTheDocument();
  expect(within(chart).getByText("D0")).toBeInTheDocument();
  expect(within(chart).getByText("D3")).toBeInTheDocument();
});

test("`max` keeps the scale when the data in view is smaller", () => {
  render(<BarChart label="x" bars={bars([5, 10])} max={58} />);
  expect(within(screen.getByRole("img")).getByText("60")).toBeInTheDocument();
});

test("bars are at most 24px thick, and a taller value is a taller bar", () => {
  const { container } = render(<BarChart label="x" bars={bars([10, 20])} />);
  const [short, tall] = [...container.querySelectorAll("[data-bar]")].map((p) => p.getAttribute("d") ?? "");
  const top = (d: string) => Number(/V([\d.]+)A/.exec(d)?.[1]); // where the rounded end starts: a smaller y is higher up
  expect(top(tall)).toBeLessThan(top(short));
  const width = (d: string) => Number([...d.matchAll(/0 0 1 ([\d.]+),/g)].at(-1)?.[1]) - Number(/^M([\d.]+),/.exec(d)?.[1]);
  expect(width(tall)).toBeCloseTo(24);
});

test("the marker is a line after the bar it names", () => {
  const { container, rerender } = render(<BarChart label="x" bars={bars([1, 2, 3, 4])} marker="d1" />);
  const line = container.querySelector("[data-marker]");
  expect(Number(line?.getAttribute("x1"))).toBeCloseTo(52 + 2 * ((720 - 52 - 12) / 4)); // the right edge of the second of four
  rerender(<BarChart label="x" bars={bars([1, 2, 3, 4])} marker={null} />);
  expect(container.querySelector("[data-marker]")).toBeNull();
});

test("hover is reported by key, and null when the pointer leaves", () => {
  const onHover = vi.fn();
  render(<BarChart label="x" bars={bars([1, 2, 3])} onHover={onHover} formatValue={(n) => `${n} units`} />);
  const target = screen.getByText("Day 1: 2 units", { selector: "title" }).parentElement!; // the whole day is the target, with a native tooltip
  fireEvent.mouseEnter(target);
  expect(onHover).toHaveBeenLastCalledWith("d1");
  fireEvent.mouseLeave(screen.getByRole("img"));
  expect(onHover).toHaveBeenLastCalledWith(null);
});

test("with no bars it says there is nothing to plot", () => {
  render(<BarChart label="x" bars={[]} />);
  expect(screen.getByText("Nothing to plot yet.")).toBeInTheDocument();
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});
