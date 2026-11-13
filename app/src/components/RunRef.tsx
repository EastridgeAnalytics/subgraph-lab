// Which run a screen is about, for the screens that follow one run (Run, Review and push): `?run=` in the address
// wins, then the run in the run context. The id goes into API paths, so one that is not a plain id is refused.
import { Link, useSearchParams } from "react-router-dom";
import { useRunId } from "../state/run";
import { Notice } from "./ui";

// Starts with a letter or digit and holds only letters, digits, dots, dashes and underscores: never a path.
const RUN_ID = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

export interface RunRef {
  /** The run to show, or null when there is none or the id is not valid. */
  runId: string | null;
  /** True when something was given as a run id and it is not valid. */
  invalid: boolean;
  /** A path on this app that keeps following this run: `?run=` is added only when the run came from the address. */
  withRun: (path: string) => string;
}

export function useRunRef(): RunRef {
  const [params] = useSearchParams();
  const [contextId] = useRunId();
  const fromAddress = params.get("run") || null;
  const given = fromAddress ?? contextId;
  const runId = given && RUN_ID.test(given) ? given : null;
  return {
    runId,
    invalid: Boolean(given) && runId === null,
    withRun: (path) => (runId && fromAddress ? `${path}?run=${encodeURIComponent(runId)}` : path),
  };
}

/** What a run screen shows when it has no run to follow: a way to get one. */
export function NoRunNotice({ invalid }: { invalid: boolean }) {
  const link = (
    <Link to="/slice" className="font-medium text-indigo-700 underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600">
      Slice
    </Link>
  );
  return invalid
    ? <Notice kind="warning" title="That is not a run id">The run id in the address is not one the lab makes. Start a run from {link}.</Notice>
    : <Notice kind="info" title="No run yet">There is nothing to show until a run starts. Pick a slice and start one from {link}.</Notice>;
}
