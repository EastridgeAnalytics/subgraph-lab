"""`python -m lab serve`: the API, the lab's MCP tools at /mcp, the built app at /, and Relate serving the current run.

Where it listens. Inside the container (`LAB_RELATE_HOST` is 0.0.0.0, which compose sets) it listens on 0.0.0.0, port 8000:
compose publishes that port on 127.0.0.1 as `LAB_PORT`, so `LAB_PORT` is a host port there, like Relate's. Outside the
container it listens on 127.0.0.1, port `LAB_PORT`.

The built app comes from `LAB_APP_DIST` (the Dockerfile sets `/srv/app/dist`), else `app/dist` beside where the lab is
started. This is the only place that reads it.
"""
from __future__ import annotations

from pathlib import Path

from lab.api.relate_server import in_container, serve_ports
from lab.common.log import get_logger
from lab.settings import get_settings

log = get_logger("api.server")


def app_dist() -> Path | None:
    """The folder with the built app, or None (with a warning) when there is no `index.html` in it."""
    dist = Path(get_settings().app_dist)
    if (dist / "index.html").is_file():
        return dist
    log.warning("no built app at %s: only /api and /mcp are served. Run `npm run build` in app/ or set LAB_APP_DIST.", dist)
    return None


def serve() -> int:
    import uvicorn

    from lab.api.app import create_app

    settings = get_settings()
    host = "0.0.0.0" if in_container(settings) else "127.0.0.1"  # noqa: S104 (inside the container, published on 127.0.0.1)
    port = serve_ports(settings)["lab"]
    log.info("subgraph-lab on http://%s:%s (data in %s)", host, port, settings.data_dir)
    uvicorn.run(create_app(app_dist=app_dist()), host=host, port=port, log_level="info")
    return 0
