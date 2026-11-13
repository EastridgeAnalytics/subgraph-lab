"""The lab's API and agent surface (page 06): FastAPI routes, the eight MCP tools at /mcp, the worker that runs
recipes, the `relate serve --read-only=live` the lab supervises, and the built app at /.

`server.serve()` starts all of it (`python -m lab serve`). `app.create_app()` builds the FastAPI app; `ops` is what its
routes and the tools both do; `results` reads a run; `worker`, `relate_server` and `connection` are the three pieces of
state an app holds.
"""
