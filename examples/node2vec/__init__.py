"""The Node2Vec example (page 03). `from examples.node2vec import embed` is the one place the training loop lives.

The names load on first use, so that `python -m examples.node2vec.example` does not import the example twice.
"""
from __future__ import annotations

__all__ = ["EPOCHS", "embed"]


def __getattr__(name: str):
    if name in __all__:
        from examples.node2vec import example

        return getattr(example, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
