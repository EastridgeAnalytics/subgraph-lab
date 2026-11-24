"""The lab's nine MCP tools (page 06), on the same functions as the REST routes.

`lab.contracts.tools.TOOLS` names them, with each one's input and output model. Each tool takes its input model's
fields as its arguments (an agent sends `{"run_id": ..., "confirm": true}`, not a wrapper) and returns its output model,
so the schemas an agent reads are the contract's.

`apply_push` refuses unless `confirm` is true (A5), before anything is opened. A failure an agent should read comes back as
a tool error with one plain sentence; the same sentence a route would put in `{"detail": ...}`.

A field's constraints go into the tool's signature with its type (decision 50). The SDK checks a call's arguments
against a model of its own, built from that signature, before the contract's model sees them. With the type alone it
would read `"yes"` or `1` as `confirm` true.
"""
from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel

from lab.api import ops
from lab.api.errors import as_api_error
from lab.contracts.tools import TOOLS

INSTRUCTIONS = (
    "subgraph-lab: pull a slice of the master graph into a run store (egress, queued: poll run_status until "
    "first_pass is done), search it (search_slice), score it (score, queued: poll run_status until score is done), "
    "read the dry-run plan (preview_push), and push (apply_push, which needs confirm set to the JSON boolean true and "
    "writes to the master graph). Read the subgraph itself through Relate's own MCP server, read-only."
)


def implementations(state: ops.LabState) -> dict[str, Callable[[Any], BaseModel]]:
    """Tool name to the function that does it, taking the tool's input model."""
    return {
        "egress": lambda i: ops.egress(state, i),
        "run_status": lambda i: ops.run_status(i.run_id, i.wait_seconds),
        "search_slice": lambda i: ops.search_slice(state, i),
        "score": lambda i: ops.score(state, i.run_id),
        "preview_push": lambda i: ops.review(state, i.run_id),
        "apply_push": lambda i: ops.push(state, i.run_id, i.confirm),
        "explain_alert": lambda i: ops.explain(i.run_id, i.key),
        "describe_alert": lambda i: ops.describe(state, i),
        "graph_diff": lambda i: ops.graph_diff(i),
    }


def _tool(name: str, model_in: type[BaseModel], model_out: type[BaseModel], call: Callable[[Any], BaseModel],
          description: str) -> Callable[..., BaseModel]:
    """A function whose signature is the input model's fields and whose return type is the output model: what the SDK
    turns into the tool's input and output schemas."""

    def tool(**kwargs: Any) -> BaseModel:
        try:
            return call(model_in(**kwargs))
        except Exception as e:  # noqa: BLE001 (every failure becomes the sentence an agent can read)
            raise ToolError(as_api_error(e).detail) from e

    params = [
        inspect.Parameter(
            field, inspect.Parameter.KEYWORD_ONLY,
            annotation=Annotated[(info.annotation, *info.metadata)] if info.metadata else info.annotation,
            default=inspect.Parameter.empty if info.is_required() else info.get_default(call_default_factory=True),
        )
        for field, info in model_in.model_fields.items()
    ]
    tool.__signature__ = inspect.Signature(params, return_annotation=model_out)  # type: ignore[attr-defined]
    tool.__name__, tool.__doc__ = name, description
    return tool


def build_mcp(state: ops.LabState) -> MCPServer:
    """The nine tools on one MCP server, ready to be served over streamable HTTP."""
    server = MCPServer("subgraph-lab", instructions=INSTRUCTIONS)
    calls = implementations(state)
    for name, (model_in, model_out, description) in TOOLS.items():
        server.add_tool(_tool(name, model_in, model_out, calls[name], description), name=name, description=description)
    return server
