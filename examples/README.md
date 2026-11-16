# examples

Four open-source models on the slice. Three are the library's own example with the data swapped for the slice and
the ending changed to two lines, `lab.stage(df)` and `lab.push()`. Each is under 100 lines and runs on CPU. Page
[03](../architecture-docs/03-models-and-adapters.md) says how the slice reaches them. The point is the ending.
Bring any model, stage its table, push.

| Example | Taken from | Gives | Test |
|---|---|---|---|
| `node2vec/` | PyTorch Geometric's `examples/node2vec.py` | An embedding per account; the first pass runs it on every pull | `tests/test_first_pass.py` |
| `tgn/` | PyTorch Geometric's `examples/tgn.py` | A temporal score per account, registered as the `tgn` factor | `tests/test_tgn.py` |
| `velocity/` | Nixtla's statsforecast quick start | A forecast per account, registered as the `velocity_anomaly` factor | `tests/test_velocity.py` |
| `networkx/` | NetworkX's `louvain_communities` and `simple_cycles`, on the lab's own community and cycle code | Communities and money cycles | `tests/test_networkx_example.py` |

Each file's docstring says what changed from the library's version.
