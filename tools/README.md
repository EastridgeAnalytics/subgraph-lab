# tools

| Tool | Does |
|---|---|
| `install_relate.py` | Downloads the Relate CLI for this platform, checks it against the release's `SHA256SUMS`, and puts it on the venv's path. The download is written beside its destination and renamed into place, so it works with the venv on any drive. The Dockerfile does the same for Linux |
| `measure.py` | The measurements behind [98](../architecture-docs/98-measured.md): each run writes a record to `architecture-docs/measured/` with the machine, the command and the values, and the page cites the record |
| `check_docs.py` | Checks every Markdown page: links resolve, no banned names, no filler words, at most one prose dash and one ampersand. With `--mermaid`, a page that renders every diagram |
| `check_licenses.py` | Every Python distribution in the image with its licence, from its own metadata. Fails on copyleft |
| `export_schemas.py` | Writes every contract's JSON schema to `lab/contracts/schemas/`; `tests/test_contracts.py` fails when the files and the models disagree |

Run them with `uv run python tools/<name>.py`. `check_docs.py` runs on every docs change.
