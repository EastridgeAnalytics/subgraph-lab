"""The TGN example (page 03). `from examples.tgn import run_model` is the one place the training loop lives.

The names load on first use, so that `python -m examples.tgn.example` does not import the example twice.
"""
from __future__ import annotations

__all__ = ["run_model"]


def __getattr__(name: str):
    if name in __all__:
        from examples.tgn import example

        return getattr(example, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
