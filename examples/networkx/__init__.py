"""The NetworkX example (page 03). `from examples.networkx import cycles_by_account` is the one place cycles are counted.

The names load on first use, so that `python -m examples.networkx.example` does not import the example twice.
"""
from __future__ import annotations

__all__ = ["cycles_by_account"]


def __getattr__(name: str):
    if name in __all__:
        from examples.networkx import example

        return getattr(example, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
