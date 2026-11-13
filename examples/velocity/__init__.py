"""The velocity example (page 03). `from examples.velocity import risk_velocity` gives the staged table.

The forecasting itself is `lab.scoring.velocity.score`, the function `factors.yaml` names as `velocity_anomaly`, so
the factor runs in one place. The names load on first use, so that `python -m examples.velocity.example` does not
import the example twice (and statsforecast, which takes seconds to import, only loads when something is run).
"""
from __future__ import annotations

__all__ = ["risk_velocity"]


def __getattr__(name: str):
    if name in __all__:
        from examples.velocity import example

        return getattr(example, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
