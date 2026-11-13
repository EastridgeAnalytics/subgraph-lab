// A side panel of code, in the style of the header's Show code: a titled dialog on the right, tabs for each piece of
// text, a Copy button, Escape and Close to leave, focus kept inside while it is open and returned to the button that
// opened it. Unlike the header's, this one is told what to show: the Models screen uses it for one model or one factor.
// It renders nothing while `open` is false.
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Button, Tabs } from "./ui";

const FOCUSABLE = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

export interface CodeTab { id: string; label: string; text: string }

export function CodeDrawer({ open, title, description, path, notice, tabs, onClose }: {
  open: boolean;
  title: string;
  description?: ReactNode;
  /** Where the code lives, shown above the tabs as a path to copy. */
  path?: string;
  /** Anything the reader should know before the text, such as what this panel cannot show. */
  notice?: ReactNode;
  tabs: CodeTab[];
  onClose: () => void;
}) {
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  const [tab, setTab] = useState(tabs[0]?.id ?? "");
  const [copy, setCopy] = useState<"idle" | "copied" | "failed">("idle");
  const timer = useRef<ReturnType<typeof setTimeout>>();

  // Each time it opens, start on the first tab.
  useEffect(() => { if (open) { setTab(tabs[0]?.id ?? ""); setCopy("idle"); } }, [open]);
  useEffect(() => () => clearTimeout(timer.current), []);

  useEffect(() => {
    if (!open) return;
    const before = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); close.current(); return; }
      if (e.key !== "Tab" || !panelRef.current) return;
      const items = Array.from(panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => !el.hasAttribute("disabled"));
      if (items.length === 0) return;
      const first = items[0], last = items[items.length - 1];
      const inside = panelRef.current.contains(document.activeElement);
      if (!inside || (e.shiftKey && document.activeElement === first)) { e.preventDefault(); (e.shiftKey ? last : first).focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); before?.focus(); };
  }, [open]);

  if (!open) return null;
  const shown = tabs.find((t) => t.id === tab) ?? tabs[0];
  const onCopy = async () => {
    clearTimeout(timer.current);
    try { await navigator.clipboard.writeText(shown?.text ?? ""); setCopy("copied"); } catch { setCopy("failed"); }
    timer.current = setTimeout(() => setCopy("idle"), 2000);
  };

  return createPortal(
    <div className="fixed inset-0 z-50">
      <div className="absolute inset-0 bg-slate-900/30" aria-hidden="true" onClick={() => close.current()} />
      <div ref={panelRef} role="dialog" aria-modal="true" aria-labelledby={titleId}
        className="absolute inset-y-0 right-0 flex w-full max-w-2xl flex-col gap-4 overflow-y-auto bg-white p-6 shadow-xl">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 id={titleId} className="text-lg font-semibold text-slate-900">{title}</h2>
            {description && <p className="mt-1 text-sm text-slate-600">{description}</p>}
          </div>
          <Button ref={closeRef} onClick={() => close.current()}>Close</Button>
        </div>
        {path && <p className="text-sm text-slate-600">Lives at <code className="break-all text-slate-900">{path}</code></p>}
        {notice}
        <Tabs tabs={tabs.map(({ id, label }) => ({ id, label }))} value={shown?.id ?? ""} onChange={(t) => { setTab(t); setCopy("idle"); }} label="Code">
          <div className="space-y-3">
            <div className="flex items-center justify-end gap-3">
              <span role="status" className="text-sm text-slate-600">
                {copy === "copied" ? "Copied to the clipboard." : copy === "failed" ? "Could not copy. Select the text and copy it." : ""}
              </span>
              <Button onClick={onCopy} disabled={!shown}>{copy === "copied" ? "Copied" : "Copy"}</Button>
            </div>
            <pre tabIndex={0} aria-label={shown?.label ?? "Code"}
              className="max-h-[60vh] overflow-auto rounded-md border border-slate-200 bg-slate-50 p-4 text-sm leading-relaxed text-slate-800">
              <code>{shown?.text ?? ""}</code>
            </pre>
          </div>
        </Tabs>
      </div>
    </div>,
    document.body);
}
