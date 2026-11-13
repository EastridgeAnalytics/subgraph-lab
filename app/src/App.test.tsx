import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App, { SCREENS } from "./App";
import { api, setMock } from "./api/client";

test("the nav lists the seven screens in the order of the loop", () => {
  render(<MemoryRouter initialEntries={["/connect"]}><App /></MemoryRouter>);
  expect(SCREENS.map((s) => s.label)).toEqual(
    ["Connect", "Slice", "Models", "Run", "Results", "Review and push", "Timeline"]);
  for (const s of SCREENS) expect(screen.getByRole("link", { name: s.label })).toBeInTheDocument();
});

test("the mock switch answers without the lab", async () => {
  setMock(true);
  expect((await api.health()).relate).toBe("1.0.1");
  await expect(api.models()).rejects.toThrow(/no mock/);
  setMock(null);
});
