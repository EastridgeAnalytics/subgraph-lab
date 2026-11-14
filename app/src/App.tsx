import type { ReactElement } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { isMock } from "./api/client";
import { ShowCode } from "./components/ShowCode";
import Connect from "./screens/Connect";
import Models from "./screens/Models";
import Results from "./screens/Results";
import Review from "./screens/Review";
import Run from "./screens/Run";
import Slice from "./screens/Slice";
import Timeline from "./screens/Timeline";
import { RunProvider } from "./state/run";

// The seven MVP screens (page 06). Each screen lives in src/screens/<Name>.tsx and is filled in by its track.
// The order of the nav is the order of the loop.
export const SCREENS = [
  { path: "/connect", label: "Connect" },
  { path: "/slice", label: "Slice" },
  { path: "/models", label: "Models" },
  { path: "/run", label: "Run" },
  { path: "/results", label: "Results" },
  { path: "/review", label: "Review and push" },
  { path: "/timeline", label: "Timeline" },
] as const;

// The screens that are built. A track adds its screen here; the rest show the placeholder.
const BUILT: Record<string, ReactElement> = {
  "/connect": <Connect />,
  "/slice": <Slice />,
  "/models": <Models />,
  "/run": <Run />,
  "/results": <Results />,
  "/review": <Review />,
  "/timeline": <Timeline />,
};

function Placeholder({ label }: { label: string }) {
  return <p className="text-slate-500">{label} is not built yet.</p>;
}

export default function App() {
  return (
    <RunProvider>
      <div className="min-h-screen bg-white text-slate-800">
        <header className="flex items-center gap-6 border-b border-slate-200 px-6 py-3">
          <span className="flex shrink-0 items-center gap-2 whitespace-nowrap font-semibold text-slate-900">
            <span aria-hidden="true" className="h-2.5 w-2.5 rounded-full bg-indigo-600" />
            subgraph-lab
          </span>
          {/* When the header is too narrow the nav wraps between items, never inside a label: each link is nowrap. */}
          <nav aria-label="Screens" className="flex flex-wrap gap-1 text-sm">
            {SCREENS.map((s) => (
              <NavLink key={s.path} to={s.path}
                className={({ isActive }) =>
                  "whitespace-nowrap rounded-md px-3 py-1.5 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 " +
                  (isActive ? "bg-indigo-50 font-medium text-indigo-700" : "text-slate-600 hover:bg-slate-50 hover:text-slate-900")}>
                {s.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex shrink-0 items-center gap-3 whitespace-nowrap">
            {isMock() && (
              <span className="rounded-full border border-amber-300 bg-amber-50 px-2.5 py-0.5 text-xs font-medium text-amber-900">
                Mock data
              </span>
            )}
            <ShowCode />
          </div>
        </header>
        <main className="max-w-6xl px-6 py-6">
          <Routes>
            <Route path="/" element={<Navigate to="/connect" replace />} />
            {SCREENS.map((s) => (
              <Route key={s.path} path={s.path} element={BUILT[s.path] ?? <Placeholder label={s.label} />} />
            ))}
          </Routes>
        </main>
      </div>
    </RunProvider>
  );
}
