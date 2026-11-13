"""Three formats out (page 03): a table, a PyTorch Geometric object, a NetworkX graph, and a series frame.

Each adapter takes the run first, as `lab/runs.py` calls it, and reads the run store through Relate. `keys` is the
slice, a list of account keys; None is the whole run store. torch, PyG and NetworkX load when an adapter that
needs them runs, not when this package is imported.
"""
from lab.adapters.nx import to_networkx
from lab.adapters.pyg import PygGraph, to_pyg
from lab.adapters.series import to_series
from lab.adapters.tabular import table
from lab.adapters.temporal import TemporalGraph, to_temporal

__all__ = ["PygGraph", "TemporalGraph", "table", "to_networkx", "to_pyg", "to_series", "to_temporal"]
