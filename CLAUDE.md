Read `architecture-docs/README.md` first, then the pages it lists, before reading or changing any code.

`architecture-docs/99-build-status.md` says what is built, partly built and not. A change that builds, changes or
removes a piece updates that page in the same commit. A change that makes any page wrong changes the page in the
same commit.

Building it? `architecture-docs/97-development-plan.md` is the plan. The director runs it; builders read their
brief and the pages it names.

`demo/` is the judge-facing walkthrough and the committed store. `demo/build_walkthrough.py` remakes the pages and
figures from the screenshots that `demo/walk.py` takes; change the text there, not in the generated files.

Never write a number that has not been measured as if it had been. Tag it "to measure". When it is measured, say
which run produced it.

Nothing reads from or writes to the master graph except `relate pull`, `relate push` and the Bolt fallback in
`lab/`, and `apply_push` with confirm set is the only path that writes.
