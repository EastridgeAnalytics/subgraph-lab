import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App, { SCREENS } from "./App";
import { api, setMock } from "./api/client";

afterEach(() => setMock(null));

test("the nav lists the seven screens in the order of the loop", () => {
  setMock(true);
  render(<MemoryRouter initialEntries={["/connect"]}><App /></MemoryRouter>);
  expect(SCREENS.map((s) => s.label)).toEqual(
    ["Connect", "Slice", "Models", "Run", "Results", "Review and push", "Timeline"]);
  for (const s of SCREENS) expect(screen.getByRole("link", { name: s.label })).toBeInTheDocument();
});

test("the mock switch answers without the lab", async () => {
  setMock(true);
  expect((await api.health()).relate).toBe("1.0.1");
  await expect(api.resume("run-unknown")).rejects.toThrow(/no mock/); // a route no screen has a mock for
  setMock(null);
});

// jsdom has no layout, so this checks the classes that decide the wrapping. By arithmetic at text-sm: the seven links are
// about 550 px wide in one row (the longest, "Review and push", about 130). At 1280 px the header has about 960 px of
// content, so the nav is one row. At 740 px the brand and the right-hand buttons keep their width (shrink-0) and the
// nav gets about 320 px, so it wraps onto a second or third row between whole links, none of them broken inside.
test("the nav wraps between items and never inside a label, at any width", () => {
  setMock(true);
  render(<MemoryRouter initialEntries={["/connect"]}><App /></MemoryRouter>);
  const nav = screen.getByRole("navigation", { name: "Screens" });
  expect(nav).toHaveClass("flex", "flex-wrap");
  const links = within(nav).getAllByRole("link");
  expect(links).toHaveLength(SCREENS.length);
  for (const link of links) {
    expect(link).toHaveClass("whitespace-nowrap"); // a label stays on one line
    expect(link.childElementCount).toBe(0); // and is one piece of text, so there is nowhere inside it to break
  }
  expect(within(nav).getByRole("link", { name: "Review and push" })).toHaveClass("whitespace-nowrap");
  expect(within(nav).getByRole("link", { name: "Connect" })).toHaveAttribute("aria-current", "page"); // the current one too
  // the rest of the header keeps its width, so the nav is what gives way
  expect(screen.getByText("subgraph-lab")).toHaveClass("shrink-0", "whitespace-nowrap");
  expect(screen.getByRole("button", { name: "Show code" }).parentElement).toHaveClass("shrink-0", "whitespace-nowrap");
});
