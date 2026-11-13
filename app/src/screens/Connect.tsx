// Connect (page 06): pick the graph the lab pulls from, test it, and see which pull route it will use.
// The one decision: use this connection. The password lives in this component's state only, goes to the lab on
// Test and Use, is cleared once the connection is in use, and is never written to localStorage or shown back.
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ConnectResult, ConnectTest, Route } from "../api/types";
import { errorMessage } from "../components/format";
import { Button, Card, Field, Notice, Page, Stat } from "../components/ui";

type Choice = "bundled" | "aura";

const ROUTE: Record<Route, string> = {
  relate: "relate pull, over the Query API",
  bolt: "Bolt, parallel reads (the fallback)",
};

function Details({ result }: { result: ConnectResult }) {
  return (
    <div className="space-y-4">
      <Notice kind={result.ok ? "success" : "error"} title={result.ok ? "Connected" : "Not connected"}>{result.message}</Notice>
      {result.ok && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Stat label="Neo4j version" value={result.neo4j_version ?? "—"} />
          <Stat label="Edition" value={result.edition ?? "—"} />
          <Stat label="Query API address" value={<span className="break-all text-base">{result.query_uri ?? "—"}</span>} />
          <Stat label="Pull route" value={<span className="text-base">{result.pull_route ? ROUTE[result.pull_route] : "—"}</span>} />
        </div>
      )}
    </div>
  );
}

export default function Connect() {
  const [current, setCurrent] = useState<ConnectResult | null>(null);
  const [currentError, setCurrentError] = useState<string | null>(null);

  const [choice, setChoice] = useState<Choice>("bundled");
  const [uri, setUri] = useState("");
  const [username, setUsername] = useState("neo4j"); // Neo4j's own defaults
  const [password, setPassword] = useState("");
  const [database, setDatabase] = useState("neo4j");

  const [tested, setTested] = useState<ConnectResult | null>(null); // the result for the inputs as they are now
  const [testing, setTesting] = useState(false);
  const [using, setUsing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [switched, setSwitched] = useState(false);

  useEffect(() => {
    let live = true;
    api.connection()
      .then((r) => { if (live) setCurrent(r); })
      .catch((e) => { if (live) setCurrentError(errorMessage(e)); });
    return () => { live = false; };
  }, []);

  // Any edit makes the last test stale, so "Use this connection" can only ever use what was tested.
  const edit = <T,>(set: (v: T) => void) => (v: T) => { set(v); setTested(null); setError(null); setSwitched(false); };

  // Nothing given means the lab's own settings (the bundled Neo4j unless .env says otherwise).
  const body = (): ConnectTest =>
    choice === "bundled" ? {} : { uri: uri.trim(), username: username.trim(), password, database: database.trim() };

  const ready = choice === "bundled" || (uri.trim() !== "" && password !== "");
  const passed = tested?.ok === true;

  const test = async () => {
    setTesting(true); setError(null); setTested(null);
    try { setTested(await api.connectTest(body())); }
    catch (e) { setError(errorMessage(e)); }
    finally { setTesting(false); }
  };

  const use = async () => {
    setUsing(true); setError(null); setSwitched(false);
    try {
      const r = await api.connectUse(body());
      if (r.ok) { setCurrent(r); setTested(null); setPassword(""); setSwitched(true); }
      else setError(r.message);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setUsing(false);
    }
  };

  return (
    <Page title="Connect" purpose="Pick the graph the lab pulls from. Test it first; the lab tells you which pull route it will use.">
      <Card title="In use now">
        {currentError ? <Notice kind="error" title="Could not read the connection in use">{currentError}</Notice>
          : current ? <Details result={current} />
          : <p role="status" className="text-sm text-slate-600">Reading the connection in use…</p>}
      </Card>

      <Card title="Choose a connection">
        <fieldset className="space-y-3">
          <legend className="text-sm font-medium text-slate-800">Where is the graph?</legend>
          <label className="flex items-start gap-3">
            <input type="radio" name="connection" checked={choice === "bundled"} onChange={() => edit(setChoice)("bundled")}
              className="mt-1 accent-indigo-600" />
            <span>
              <span className="block text-sm font-medium text-slate-800">The bundled Neo4j</span>
              <span className="block text-sm text-slate-500">The lab's own settings, the .env file. By default that is the Neo4j that comes with the lab.</span>
            </span>
          </label>
          <label className="flex items-start gap-3">
            <input type="radio" name="connection" checked={choice === "aura"} onChange={() => edit(setChoice)("aura")}
              className="mt-1 accent-indigo-600" />
            <span>
              <span className="block text-sm font-medium text-slate-800">My own Neo4j or Aura</span>
              <span className="block text-sm text-slate-500">Paste its address and password.</span>
            </span>
          </label>
        </fieldset>

        {choice === "aura" && (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <Field label="URI" value={uri} onChange={(e) => edit(setUri)(e.target.value)}
                placeholder="neo4j+s://xxxxxxxx.databases.neo4j.io" autoComplete="off" spellCheck={false}
                hint="The connection address from Aura, or bolt:// for a local Neo4j." />
            </div>
            <Field label="Username" value={username} onChange={(e) => edit(setUsername)(e.target.value)} autoComplete="off"
              hint="Default neo4j." />
            <Field label="Database" value={database} onChange={(e) => edit(setDatabase)(e.target.value)} autoComplete="off"
              hint="Default neo4j." />
            <div className="sm:col-span-2">
              <Field label="Password" type="password" value={password} onChange={(e) => edit(setPassword)(e.target.value)}
                autoComplete="new-password"
                hint="Sent to the lab, which keeps it in memory for this session only. This page never shows it back or saves it." />
            </div>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-3">
          <Button variant={passed ? "secondary" : "primary"} onClick={test} busy={testing} disabled={!ready || using}>
            Test connection
          </Button>
          <Button variant="primary" onClick={use} busy={using} disabled={!passed || testing}>
            Use this connection
          </Button>
          {!ready && <span className="text-sm text-slate-500">Give the URI and the password to test.</span>}
          {ready && !passed && !testing && <span className="text-sm text-slate-500">Test it first. Use unlocks when the test passes.</span>}
        </div>

        {error && <Notice kind="error" title="That did not work">{error}</Notice>}
        {switched && <Notice kind="success" title="Connection switched">The lab uses it from now on, for this session.</Notice>}
      </Card>

      {tested && (
        <Card title="Test result"><Details result={tested} /></Card>
      )}
    </Page>
  );
}
