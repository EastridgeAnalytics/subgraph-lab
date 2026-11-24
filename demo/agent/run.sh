#!/usr/bin/env sh
# The runner is demo/agent/run.py, which works from any shell (decision 51). This keeps the old command working.
exec uv run python "$(dirname "$0")/run.py" "$@"
