// A piece of text kept in localStorage, like useState: `const [name, setName] = useStoredText("key")`.
// Storage that is blocked or full still works for the life of the page.
import { useCallback, useState } from "react";

export function useStoredText(key: string): [string, (value: string) => void] {
  const [value, setValue] = useState(() => {
    try { return window.localStorage.getItem(key) ?? ""; } catch { return ""; }
  });
  const set = useCallback((next: string) => {
    setValue(next);
    try { window.localStorage.setItem(key, next); } catch { /* blocked or full: the value stays for this page */ }
  }, [key]);
  return [value, set];
}
