import { NavLink, Navigate, Route, Routes } from "react-router-dom";

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

function Placeholder({ label }: { label: string }) {
  return <p className="text-slate-500">{label} is not built yet.</p>;
}

export default function App() {
  return (
    <div className="min-h-screen bg-white">
      <header className="border-b border-slate-200 px-6 py-3 flex items-center gap-6">
        <span className="font-semibold">subgraph-lab</span>
        <nav className="flex gap-4 text-sm">
          {SCREENS.map((s) => (
            <NavLink key={s.path} to={s.path}
              className={({ isActive }) => (isActive ? "text-indigo-700 font-medium" : "text-slate-600 hover:text-slate-900")}>
              {s.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main className="px-6 py-6 max-w-6xl">
        <Routes>
          <Route path="/" element={<Navigate to="/connect" replace />} />
          {SCREENS.map((s) => <Route key={s.path} path={s.path} element={<Placeholder label={s.label} />} />)}
        </Routes>
      </main>
    </div>
  );
}
