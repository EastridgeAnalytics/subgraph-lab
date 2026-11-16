# app

The seven screens: React 18, Vite, TypeScript and Tailwind. Page
[06](../architecture-docs/06-the-app-and-the-agent.md) says what each one shows and why.

| Screen | File | Does |
|---|---|---|
| Connect | `src/screens/Connect.tsx` | Test the master graph and see the route the pull will take |
| Models | `src/screens/Models.tsx` | Pick the factors and models, read each one's code, set weights and thresholds |
| Slice | `src/screens/Slice.tsx` | By schema, by Cypher or by search. The estimate comes first. Then Run opens |
| Run | `src/screens/Run.tsx` | One bar per step with live counts; resume on failure |
| Results | `src/screens/Results.tsx` | Accounts and communities. A community's members, score parts, summary, evidence, communities like it, and the decision buttons |
| Review | `src/screens/Review.tsx` | The dry-run plan and the delta side by side, then one confirm |
| Timeline | `src/screens/Timeline.tsx` | The store as of a time |

`src/api/client.ts` is the one client every screen uses. With `VITE_MOCK_API=1` at build time, or `?mock=1` in the
address bar, it answers from `src/api/mock.ts` and never calls the lab. `src/components/` holds the pieces the
screens share, including the "show code" drawer. `npm test` runs Vitest; `npm run build` writes `dist/`, which the
lab serves.
