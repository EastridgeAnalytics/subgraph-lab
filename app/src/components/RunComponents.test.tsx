import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { MemoryRouter } from "react-router-dom";
import { RunProvider } from "../state/run";
import { ConfirmDialog } from "./ConfirmDialog";
import { ProgressBar } from "./Progress";
import { NoRunNotice, useRunRef } from "./RunRef";

describe("ProgressBar", () => {
  test("a fraction becomes a percentage on the bar, clamped to 0 to 100", () => {
    const { rerender } = render(<ProgressBar label="Pull" value={0.4} valueText="Running" />);
    const bar = screen.getByRole("progressbar", { name: "Pull" });
    expect(bar).toHaveAttribute("aria-valuenow", "40");
    expect(bar).toHaveAttribute("aria-valuetext", "Running");
    expect((bar.firstElementChild as HTMLElement).style.width).toBe("40%");
    rerender(<ProgressBar label="Pull" value={7} />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
    rerender(<ProgressBar label="Pull" value={-1} />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "0");
  });

  test("null means working with no fraction: no value is announced", () => {
    render(<ProgressBar label="Pull" value={null} />);
    expect(screen.getByRole("progressbar")).not.toHaveAttribute("aria-valuenow");
  });
});

describe("ConfirmDialog", () => {
  function Harness({ onConfirm }: { onConfirm: () => void }) {
    const [open, setOpen] = useState(false);
    return (
      <>
        <button type="button" onClick={() => setOpen(true)}>Ask</button>
        <ConfirmDialog open={open} title="Sure?" confirmLabel="Yes, do it" onCancel={() => setOpen(false)}
          onConfirm={() => { setOpen(false); onConfirm(); }}>
          <p>This changes things.</p>
        </ConfirmDialog>
      </>
    );
  }

  test("it shows nothing until opened, then a named alert dialog with its text and two buttons", () => {
    render(<Harness onConfirm={() => {}} />);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    const dialog = screen.getByRole("alertdialog", { name: "Sure?" });
    expect(dialog).toHaveAccessibleDescription("This changes things.");
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
    expect(screen.getByRole("button", { name: "Yes, do it" })).toBeInTheDocument();
  });

  test("only the confirm button confirms; Cancel, Escape and the backdrop do not", () => {
    const onConfirm = vi.fn();
    const { baseElement } = render(<Harness onConfirm={onConfirm} />);
    const ask = screen.getByRole("button", { name: "Ask" });

    ask.focus(); // a real click focuses the button; fireEvent does not
    fireEvent.click(ask);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(ask).toHaveFocus(); // focus goes back to where it was

    fireEvent.click(ask);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

    fireEvent.click(ask);
    fireEvent.click(baseElement.querySelector('[aria-hidden="true"].absolute') as HTMLElement);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(onConfirm).not.toHaveBeenCalled();

    fireEvent.click(ask);
    fireEvent.click(screen.getByRole("button", { name: "Yes, do it" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  test("Tab stays inside the dialog", () => {
    render(<Harness onConfirm={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    const confirm = screen.getByRole("button", { name: "Yes, do it" });
    confirm.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(confirm).toHaveFocus();
  });
});

describe("useRunRef and NoRunNotice", () => {
  function Show() {
    const { runId, invalid, withRun } = useRunRef();
    return <p data-testid="out">{`${runId}|${invalid}|${withRun("/results")}`}</p>;
  }
  const at = (path: string) => render(<MemoryRouter initialEntries={[path]}><RunProvider><Show /></RunProvider></MemoryRouter>);

  beforeEach(() => window.localStorage.clear());

  test("?run= gives the run, and links keep following it", () => {
    at("/run?run=run-20261112T090000Z-ab12");
    expect(screen.getByTestId("out")).toHaveTextContent("run-20261112T090000Z-ab12|false|/results?run=run-20261112T090000Z-ab12");
  });

  test("no run and no address is no run, and not an invalid one", () => {
    at("/run");
    expect(screen.getByTestId("out")).toHaveTextContent("null|false|/results");
  });

  test.each(["..", "../push", "a/b", "a b", ".hidden", "x?y=1"])("%s is refused", (bad) => {
    at(`/run?run=${encodeURIComponent(bad)}`);
    expect(screen.getByTestId("out")).toHaveTextContent("null|true|/results");
  });

  test("the notice differs for a missing run and a refused one, and both point to Slice", () => {
    const { rerender } = render(<MemoryRouter><NoRunNotice invalid={false} /></MemoryRouter>);
    expect(screen.getByText("No run yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Slice" })).toHaveAttribute("href", "/slice");
    rerender(<MemoryRouter><NoRunNotice invalid /></MemoryRouter>);
    expect(screen.getByText("That is not a run id")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Slice" })).toHaveAttribute("href", "/slice");
  });
});
