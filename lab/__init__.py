"""subgraph-lab. `import lab; r = lab.run(); r.stage(df); r.push()` is the whole public surface for examples."""
import os

os.environ.setdefault("DO_NOT_TRACK", "1")

from lab.runs import Run, run  # noqa: E402

__version__ = "0.1.0"
__all__ = ["Run", "run", "__version__"]
