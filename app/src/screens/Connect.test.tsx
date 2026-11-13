import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { api, setMock } from "../api/client";
import { mockData } from "../api/mock";

beforeEach(() => { setMock(true); window.localStorage.clear(); });
afterEach(() => { setMock(null); vi.restoreAllMocks(); });

const mount = () => render(<MemoryRouter initialEntries={["/connect"]}><App /></MemoryRouter>);
const useButton = () => screen.getByRole("button", { name: "Use this connection" });
const testButton = () => screen.getByRole("button", { name: "Test connection" });

async function chooseAura(uri: string, password = "s3cret-pw") {
  fireEvent.click(screen.getByRole("radio", { name: /my own neo4j or aura/i }));
  fireEvent.change(screen.getByLabelText("URI"), { target: { value: uri } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
}

test("on load it shows the connection in use", async () => {
  mount();
  const card = await screen.findByRole("region", { name: "In use now" });
  expect(await within(card).findByText(mockData.connect.message)).toBeInTheDocument();
  expect(within(card).getByText("2026.08.1")).toBeInTheDocument();
  expect(within(card).getByText("community")).toBeInTheDocument();
  expect(within(card).getByText("http://localhost:17474")).toBeInTheDocument();
});

test("Use this connection stays off until a test passes, and the result shows what the lab found", async () => {
  mount();
  expect(useButton()).toBeDisabled();
  fireEvent.click(testButton());

  const result = await screen.findByRole("region", { name: "Test result" });
  expect(within(result).getByText("Connected")).toBeInTheDocument();
  expect(within(result).getByText("2026.08.1")).toBeInTheDocument();
  expect(within(result).getByText("community")).toBeInTheDocument();
  expect(within(result).getByText("http://localhost:17474")).toBeInTheDocument();
  expect(within(result).getByText(/relate pull, over the Query API/)).toBeInTheDocument();
  expect(within(result).getByText(mockData.connect.message)).toBeInTheDocument();
  expect(useButton()).toBeEnabled();
});

test("the bundled choice tests with the lab's own settings, so it sends nothing", async () => {
  const test = vi.spyOn(api, "connectTest");
  const use = vi.spyOn(api, "connectUse");
  mount();
  fireEvent.click(testButton());
  await screen.findByRole("region", { name: "Test result" });
  fireEvent.click(useButton());
  await screen.findByText("Connection switched");
  expect(test).toHaveBeenCalledWith({});
  expect(use).toHaveBeenCalledWith({});
});

test("a failed test shows why and leaves Use off", async () => {
  mount();
  await chooseAura("neo4j+s://unreachable.example.com");
  fireEvent.click(testButton());
  const result = await screen.findByRole("region", { name: "Test result" });
  expect(within(result).getByText("Not connected")).toBeInTheDocument();
  expect(within(result).getByText(mockData.connectFail.message)).toBeInTheDocument();
  expect(within(result).queryByText("Neo4j version")).not.toBeInTheDocument();
  expect(useButton()).toBeDisabled();
});

test("editing any field after a passing test turns Use off again", async () => {
  mount();
  await chooseAura("neo4j+s://abc.databases.neo4j.io");
  fireEvent.click(testButton());
  await screen.findByRole("region", { name: "Test result" });
  expect(useButton()).toBeEnabled();
  fireEvent.change(screen.getByLabelText("URI"), { target: { value: "neo4j+s://other.databases.neo4j.io" } });
  expect(useButton()).toBeDisabled();
  expect(screen.queryByRole("region", { name: "Test result" })).not.toBeInTheDocument();
});

test("Aura needs a URI and a password before it can be tested", async () => {
  mount();
  await screen.findByText(mockData.connect.message);
  fireEvent.click(screen.getByRole("radio", { name: /my own neo4j or aura/i }));
  expect(testButton()).toBeDisabled();
  fireEvent.change(screen.getByLabelText("URI"), { target: { value: "neo4j+s://abc.databases.neo4j.io" } });
  expect(testButton()).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "x" } });
  expect(testButton()).toBeEnabled();
  expect(screen.getByLabelText("Username")).toHaveValue("neo4j");
  expect(screen.getByLabelText("Database")).toHaveValue("neo4j");
});

test("the password goes to the lab, is hidden, is cleared after use, and is never stored in the browser", async () => {
  const test = vi.spyOn(api, "connectTest");
  const use = vi.spyOn(api, "connectUse");
  mount();
  await chooseAura("neo4j+s://abc.databases.neo4j.io", "s3cret-pw");
  expect(screen.getByLabelText("Password")).toHaveAttribute("type", "password");
  fireEvent.click(testButton());
  await screen.findByRole("region", { name: "Test result" });
  fireEvent.click(useButton());
  await screen.findByText("Connection switched");

  const sent = { uri: "neo4j+s://abc.databases.neo4j.io", username: "neo4j", password: "s3cret-pw", database: "neo4j" };
  expect(test).toHaveBeenCalledWith(sent);
  expect(use).toHaveBeenCalledWith(sent);
  expect(screen.getByLabelText("Password")).toHaveValue("");
  expect(document.body.innerHTML).not.toContain("s3cret-pw");
  const stored = Array.from({ length: window.localStorage.length }, (_, i) => window.localStorage.getItem(window.localStorage.key(i)!));
  expect(stored.join("|")).not.toContain("s3cret-pw");
});

test("an API error shows in a notice, not a blank screen", async () => {
  vi.spyOn(api, "connection").mockRejectedValue(new Error("lab is down"));
  vi.spyOn(api, "connectTest").mockRejectedValue(new Error("connection refused"));
  mount();
  expect(await screen.findByText("lab is down")).toBeInTheDocument();
  fireEvent.click(testButton());
  await waitFor(() => expect(screen.getByText("connection refused")).toBeInTheDocument());
  expect(screen.getAllByRole("alert")).toHaveLength(2);
  expect(useButton()).toBeDisabled();
});

test("Show code is on the screen", async () => {
  mount();
  await screen.findByText(mockData.connect.message);
  expect(screen.getByRole("button", { name: "Show code" })).toBeInTheDocument();
});
