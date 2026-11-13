// Load something once per key and keep the answer, the error and the loading state apart. A new key forgets the old
// answer at once, so a screen never shows run A's data under run B's heading. `reload()` asks again.
// Pass `null` as `load` when there is nothing to ask yet (no run): nothing is requested and `loading` is false.
import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage } from "./format";

export interface Loaded<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

export function useLoad<T>(load: (() => Promise<T>) | null, key: string): Loaded<T> {
  const [round, setRound] = useState(0);
  const [state, setState] = useState<{ at: string; data?: T; error?: string } | null>(null);
  const loadRef = useRef(load);
  loadRef.current = load; // the latest function, without asking again each time a screen re-renders
  const enabled = load !== null;
  const at = `${key}#${round}`;

  useEffect(() => {
    const ask = loadRef.current;
    if (!enabled || !ask) return;
    let live = true;
    ask().then(
      (data) => { if (live) setState({ at, data }); },
      (e) => { if (live) setState({ at, error: errorMessage(e) }); },
    );
    return () => { live = false; };
  }, [enabled, at]);

  const reload = useCallback(() => setRound((r) => r + 1), []);
  const current = state && state.at === at ? state : null;
  return {
    data: current?.data ?? null,
    error: current?.error ?? null,
    loading: enabled && current === null,
    reload,
  };
}
